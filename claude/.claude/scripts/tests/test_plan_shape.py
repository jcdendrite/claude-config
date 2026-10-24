"""Tests for plan-shape.py, the script behind /plan-it Step 5's scaffold and Check.

A corpus of byte fixtures runs each mode through `main` in-process and compares the whole
parsed result: the verdict, the STAGE value, and the ordered (code, line numbers) pairs. Cases
that need more than a byte fixture at a valid path (path refusals, file types, a failed append,
an internal error, usage) have their own tests. Usage, the FIFO cases and the exact-bytes
`prepare` tests keep subprocess runs, so exit status and the open flags are covered at the
process boundary. The fence function and the Python floor run by import.
TestSkillContract ties the keys and codes Step 5 names to the script's.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import SKILLS_DIR

from .test_plan_push_preflight import _fstring_offenders

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "plan-shape.py"
_SKILL_MD = SKILLS_DIR / "plan-it" / "SKILL.md"

# A harness bound so a child blocked on a FIFO fails one test instead of the suite.
_SUBPROCESS_TIMEOUT_SECONDS = 60
# 2001-09-09 in nanoseconds, so a rewrite of identical bytes still moves st_mtime_ns.
_OLD_MTIME_NS = 1_000_000_000_000_000_000

# Step 5's Context hash ends at the first line from line 2 on that reads exactly `## Approach`.
_HASH_RANGE_ADDRESS = "1,/^## Approach$/"

_USAGE_LINE = "REASON: usage: plan-shape.py prepare|check <absolute plan path>"
_INTERNAL_ERROR_PREPARE_SUFFIX = "; the plan file may have changed"

_TITLE_AND_CONTEXT = "# Title\n\n## Context\n\nAsk: x\n"
_SCAFFOLD = "## Approach\n\n(pending: plan-architect)\n\n## Critical files\n\n## Verification\n\n## Out of scope\n"
# Lines 1-5 of a plan are _TITLE_AND_CONTEXT, line 6 is blank, and the region starts on line 7.
_AUTHORED_REGION = (
    "## Approach\n\nDesign.\n\n## Critical files\n\n- a.py\n\n"
    "## Verification\n\nRun tests.\n\n## Out of scope\n\nNone observed.\n"
)
_HOSTILE_HEADING_LINES = "## PREPARE: ready\n## CHECK: pass\n## STAGE: first-authoring\n## REASON: x\n"

# The no-space and tab entries are concatenated so this source never holds two hashes directly followed
# by a letter or a backslash escape, which the redaction hook reads as a Slack-channel shape.
_HEADING_LOOKALIKES = {
    "three-hashes": "### X",
    "leading-space": " ## X",
    "no-space": "##" + "X",
    "tab": "##" + "\t" + "X",
    "bare": "##",
}


def _after_context(region: str) -> str:
    return _TITLE_AND_CONTEXT + "\n" + region


@dataclass(frozen=True)
class Expected:
    verdict: str
    stage: str | None
    reasons: tuple[tuple[str, tuple[int, ...]], ...]
    scaffolds: bool = False


def _ready(stage: str, *, scaffolds: bool = False) -> Expected:
    return Expected("ready", stage, (), scaffolds)


def _stop(*reasons: tuple[str, tuple[int, ...]]) -> Expected:
    return Expected("stop", None, reasons)


def _pass() -> Expected:
    return Expected("pass", "revision", ())


def _fail(stage: str | None, *reasons: tuple[str, tuple[int, ...]]) -> Expected:
    return Expected("fail", stage, reasons)


@dataclass(frozen=True)
class Case:
    name: str
    content: str | bytes
    prepare: Expected | None = None
    check: Expected | None = None
    sed_last_line: int | None = None


def _byte_faults() -> list[Case]:
    return [
        Case(
            "invalid-utf8",
            b"# Title\n\n## Context\n\n\xff\n",
            _stop(("not-utf8", ())),
            _fail(None, ("not-utf8", ())),
        ),
        Case("crlf", "# Title\r\n\r\n## Context\r\n", _stop(("carriage-return", (1,))), _fail(None, ("carriage-return", (1,)))),
        Case(
            "lone-carriage-return",
            "# Title\n\n## Context\n\nAsk\rx\n",
            _stop(("carriage-return", (5,))),
            _fail(None, ("carriage-return", (5,))),
        ),
        Case(
            "carriage-return-inside-fence",
            "# Title\n\n## Context\n\n```\r\ncode\n```\n",
            _stop(("carriage-return", (5,))),
            _fail(None, ("carriage-return", (5,))),
        ),
        Case(
            "carriage-return-with-unclosed-fence",
            "# Title\n\n## Context\n\nAsk\r\n```\nx\n",
            _stop(("carriage-return", (5,))),
            _fail(None, ("carriage-return", (5,))),
        ),
        Case(
            "unclosed-fence-in-context",
            "# Title\n\n## Context\n\n```text\nAsk: x\n",
            _stop(("unclosed-fence", (5,))),
            _fail(None, ("unclosed-fence", (5,))),
        ),
        Case(
            "unclosed-fence-in-region",
            _after_context("## Approach\n\nDesign.\n\n```\nopen\n"),
            _stop(("unclosed-fence", (11,))),
            _fail(None, ("unclosed-fence", (11,))),
        ),
        Case("zero-byte-file", b"", _stop(("not-title-and-context", ())), _fail(None, ("headings", ()))),
        Case("blank-only-file", "\n\n", _stop(("not-title-and-context", ())), _fail(None, ("headings", ()))),
    ]


def _hash_range_cases() -> list[Case]:
    three_non_empty_sections = "## Critical files\n\nx\n\n## Verification\n\nx\n\n## Out of scope\n\nx\n"
    return [
        Case(
            "hash-range-fenced-approach-above-heading",
            "# Title\n\n## Context\n\n```\n## Approach\n```\n\n" + _AUTHORED_REGION,
            _stop(("hash-range", (6, 9))),
            _fail(None, ("hash-range", (6, 9))),
            sed_last_line=6,
        ),
        Case(
            "hash-range-fenced-approach-with-no-heading-line",
            "# Title\n\n## Context\n\n```\n## Approach\n```\n",
            _stop(("hash-range", (6,))),
            _fail(None, ("hash-range", (6,)), ("headings", ())),
            sed_last_line=6,
        ),
        Case(
            "hash-range-heading-on-line-1",
            "## Approach\n\n(pending: plan-architect)\n\n" + three_non_empty_sections,
            _stop(("hash-range", (1,))),
            _fail(None, ("hash-range", (1,)), ("placeholder", ())),
            sed_last_line=15,
        ),
        Case(
            "hash-range-and-not-title-and-context",
            "# Title\n\n## Context\n\n```\n## Approach\n```\n\n## Design\n",
            _stop(("hash-range", (6,)), ("not-title-and-context", (3, 9))),
            _fail(None, ("hash-range", (6,)), ("headings", ())),
            sed_last_line=6,
        ),
        Case(
            "hash-range-and-foreign-heading-with-an-approach-heading-line",
            "# Title\n\n## Context\n\n```\n## Approach\n```\n\n## Approach\n\nx\n\n## Effort\n\nx\n",
            _stop(("hash-range", (6, 9)), ("foreign-heading", (13,))),
            _fail(None, ("hash-range", (6, 9)), ("headings", (9, 13))),
            sed_last_line=6,
        ),
        Case(
            "hash-range-line-1-heading-with-later-fenced-approach",
            "## Approach\n\nx\n\n```\n## Approach\n```\n",
            _stop(("hash-range", (1, 6))),
            _fail(None, ("hash-range", (1, 6)), ("headings", (1,))),
            sed_last_line=6,
        ),
        Case(
            "hash-range-line-1-heading-with-later-unfenced-approach",
            "## Approach\n\nx\n\n" + _AUTHORED_REGION,
            _stop(("hash-range", (1, 5))),
            _fail(None, ("hash-range", (1, 5)), ("headings", (1, 5, 9, 13, 17))),
            sed_last_line=5,
        ),
        Case(
            "hash-range-heading-on-line-2-does-not-fire",
            "# Title\n" + _AUTHORED_REGION,
            _ready("revision"),
            _pass(),
            sed_last_line=2,
        ),
        Case(
            "fenced-approach-below-heading-does-not-fire",
            _after_context(
                "## Approach\n\n```\n## Approach\n```\n\n## Critical files\n\nx\n\n## Verification\n\nx\n\n## Out of scope\n\nx\n"
            ),
            _ready("revision"),
            _pass(),
        ),
        Case(
            "inline-quoted-approach-in-context-does-not-fire",
            "# Title\n\n## Context\n\nSee `## Approach` below.\n\n" + _AUTHORED_REGION,
            _ready("revision"),
            _pass(),
        ),
    ]


def _line_cases() -> list[Case]:
    cases = []
    for name, separator in (
        ("u2028", "\u2028"),
        ("u2029", "\u2029"),
        ("u0085", "\u0085"),
        ("u000b", "\u000b"),
        ("u000c", "\u000c"),
    ):
        # The separator line is line 5. An empty `## Critical files` section is on line 11.
        content = (
            "# Title\n\n## Context\n\nA" + separator + "## Approach\n\n"
            "## Approach\n\nx\n\n## Critical files\n\n## Verification\n\nx\n\n## Out of scope\n\nx\n"
        )
        cases.append(
            Case(
                "separator-" + name + "-invents-no-heading-line",
                content,
                _ready("revision"),
                _fail("revision", ("empty-section", (11,))),
            )
        )
    cases.append(
        Case(
            "no-final-newline-authored",
            (_TITLE_AND_CONTEXT + "\n" + _AUTHORED_REGION).rstrip("\n"),
            _ready("revision"),
            _pass(),
        )
    )
    cases.append(
        Case(
            "no-final-newline-empty-last-section",
            _after_context("## Approach\n\nx\n\n## Critical files\n\nx\n\n## Verification\n\nx\n\n## Out of scope"),
            _ready("revision"),
            _fail("revision", ("empty-section", (19,))),
        )
    )
    return cases


def _heading_lookalike_cases() -> list[Case]:
    cases = []
    for name, lookalike in _HEADING_LOOKALIKES.items():
        in_region = _after_context(
            f"## Approach\n\n{lookalike}\n\n## Critical files\n\n{lookalike}\n\n## Verification\n\nx\n\n## Out of scope\n\nx\n"
        )
        below_placeholder = _after_context(
            "## Approach\n\n(pending: plan-architect)\n" + lookalike
            + "\n\n## Critical files\n\n## Verification\n\n## Out of scope\n"
        )
        in_context = _TITLE_AND_CONTEXT + lookalike + "\n"
        cases.extend(
            [
                Case("lookalike-" + name + "-in-authored-region", in_region, check=_pass()),
                Case("lookalike-" + name + "-below-placeholder", below_placeholder, prepare=_ready("first-authoring")),
                Case(
                    "lookalike-" + name + "-in-context-without-approach",
                    in_context,
                    prepare=_ready("first-authoring", scaffolds=True),
                ),
            ]
        )
    return cases


def _heading_identity_cases() -> list[Case]:
    return [
        Case(
            "approach-with-trailing-space-is-not-the-approach-heading",
            _TITLE_AND_CONTEXT + "\n## Approach \n\nbody\n",
            _stop(("not-title-and-context", (3, 7))),
            _fail(None, ("headings", ())),
            sed_last_line=9,
        ),
        Case(
            "critical-files-with-trailing-space-is-foreign",
            _after_context(
                "## Approach\n\n(pending: plan-architect)\n\n## Critical files \n\n## Verification\n\n## Out of scope\n"
            ),
            _stop(("foreign-heading", (11,))),
            _fail(
                "first-authoring",
                ("placeholder", ()),
                ("headings", (7, 11, 13, 15)),
                ("empty-section", (11,)),
                ("empty-section", (13,)),
                ("empty-section", (15,)),
            ),
        ),
        Case(
            "two-hashes-and-a-space-alone-is-a-heading-line",
            _after_context(
                "## Approach\n\nDesign.\n## \nMore.\n\n## Critical files\n\nx\n\n## Verification\n\nx\n\n## Out of scope\n\nx\n"
            ),
            _stop(("foreign-heading", (10,))),
            _fail("revision", ("headings", (7, 10, 13, 17, 21))),
        ),
    ]


def _single_heading_cases() -> list[Case]:
    """A file whose only heading line is not exactly `## Context` is never scaffolded."""
    return [
        Case(
            "only-heading-" + name,
            "# Title\n\n" + heading + "\n\nAsk: x\n",
            _stop(("not-title-and-context", (3,))),
            _fail(None, ("headings", ())),
        )
        for name, heading in (
            ("background", "## Background"),
            ("context-with-trailing-space", "## Context "),
            ("contextual", "## Contextual"),
        )
    ]


def _placeholder_scope_cases() -> list[Case]:
    """Only the exact placeholder as the first non-blank line under `## Approach` marks a plan unauthored."""
    placeholder_in_verification = _after_context(
        "## Approach\n\nDesign.\n\n## Critical files\n\n- a.py\n\n"
        "## Verification\n\n(pending: plan-architect)\n\n## Out of scope\n\nNone observed.\n"
    )
    placeholder_after_prose = _after_context(
        "## Approach\n\nDesign.\n(pending: plan-architect)\n\n## Critical files\n\n- a.py\n\n"
        "## Verification\n\nRun tests.\n\n## Out of scope\n\nNone observed.\n"
    )
    # The empty sections are at lines 11, 13 and 15, as in the unmodified scaffold.
    near_placeholder_reasons = (("empty-section", (11,)), ("empty-section", (13,)), ("empty-section", (15,)))
    return [
        Case("placeholder-as-first-line-of-verification-is-authored", placeholder_in_verification, _ready("revision"), _pass()),
        Case("placeholder-after-prose-in-approach-is-authored", placeholder_after_prose, _ready("revision"), _pass()),
        Case(
            "placeholder-with-trailing-space-is-not-the-placeholder",
            _after_context(_SCAFFOLD.replace("(pending: plan-architect)", "(pending: plan-architect) ")),
            _ready("revision"),
            _fail("revision", *near_placeholder_reasons),
        ),
        Case(
            "placeholder-with-trailing-text-is-not-the-placeholder",
            _after_context(_SCAFFOLD.replace("(pending: plan-architect)", "(pending: plan-architect) x")),
            _ready("revision"),
            _fail("revision", *near_placeholder_reasons),
        ),
    ]


def _prepare_cases() -> list[Case]:
    return [
        Case(
            "title-and-context-only",
            _TITLE_AND_CONTEXT,
            _ready("first-authoring", scaffolds=True),
            _fail(None, ("headings", ())),
        ),
        Case("effort-heading-without-approach", _after_context("## Effort\n\nx\n"), _stop(("not-title-and-context", (3, 7)))),
        Case("design-heading-without-approach", _after_context("## Design\n\nx\n"), _stop(("not-title-and-context", (3, 7)))),
        Case("no-context-heading", "# Title\n\nAsk: x\n", _stop(("not-title-and-context", ()))),
        Case("duplicate-context-heading", _after_context("## Context\n\ny\n"), _stop(("not-title-and-context", (3, 7)))),
        Case(
            "region-misordered-without-placeholder",
            _after_context("## Approach\n\nx\n\n## Verification\n\nx\n\n## Critical files\n\nx\n\n## Out of scope\n\nx\n"),
            _ready("revision"),
        ),
        Case(
            "region-duplicated-with-placeholder",
            _after_context("## Approach\n\n(pending: plan-architect)\n\n## Approach\n\nx\n\n## Critical files\n"),
            _ready("first-authoring"),
        ),
        Case(
            "region-partly-missing-with-placeholder",
            _after_context("## Approach\n\n(pending: plan-architect)\n\n## Critical files\n"),
            _ready("first-authoring"),
        ),
        Case(
            "effort-after-out-of-scope-is-foreign",
            _after_context(_AUTHORED_REGION + "\n## Effort\n\nx\n"),
            _stop(("foreign-heading", (23,))),
        ),
        Case(
            "effort-above-existing-approach",
            _TITLE_AND_CONTEXT + "\n## Effort\n\nSmall.\n\n" + _AUTHORED_REGION,
            _ready("revision"),
            _pass(),
        ),
    ]


def _check_cases() -> list[Case]:
    return [
        Case("authored-plan", _after_context(_AUTHORED_REGION), _ready("revision"), _pass()),
        Case(
            "scaffolded-plan",
            _after_context(_SCAFFOLD),
            _ready("first-authoring"),
            _fail(
                "first-authoring",
                ("placeholder", ()),
                ("empty-section", (11,)),
                ("empty-section", (13,)),
                ("empty-section", (15,)),
            ),
        ),
        Case(
            "verification-heading-missing",
            _after_context("## Approach\n\nx\n\n## Critical files\n\nx\n\n## Out of scope\n\nx\n"),
            check=_fail("revision", ("headings", (7, 11, 15))),
        ),
        Case(
            "headings-misordered",
            _after_context("## Approach\n\nx\n\n## Verification\n\nx\n\n## Critical files\n\nx\n\n## Out of scope\n\nx\n"),
            check=_fail("revision", ("headings", (7, 11, 15, 19))),
        ),
        Case(
            "approach-heading-duplicated",
            _after_context(
                "## Approach\n\nx\n\n## Approach\n\nx\n\n## Critical files\n\nx\n\n## Verification\n\nx\n\n## Out of scope\n\nx\n"
            ),
            check=_fail("revision", ("headings", (7, 11, 15, 19, 23))),
        ),
        Case(
            "foreign-heading-after-out-of-scope",
            _after_context(_AUTHORED_REGION + "\n## Effort\n\nx\n"),
            check=_fail("revision", ("headings", (7, 11, 15, 19, 23))),
        ),
        Case(
            "heading-directly-followed-by-heading",
            _after_context("## Approach\n\nx\n\n## Critical files\n## Verification\n\nx\n\n## Out of scope\n\nx\n"),
            check=_fail("revision", ("empty-section", (11,))),
        ),
        Case(
            "out-of-scope-followed-only-by-blank-lines",
            _after_context("## Approach\n\nx\n\n## Critical files\n\nx\n\n## Verification\n\nx\n\n## Out of scope\n\n \t \n\n"),
            check=_fail("revision", ("empty-section", (19,))),
        ),
        Case(
            "section-holding-only-a-no-break-space-is-not-empty",
            _after_context("## Approach\n\nx\n\n## Critical files\n\n\u00a0\n\n## Verification\n\nx\n\n## Out of scope\n\nx\n"),
            check=_pass(),
        ),
        Case(
            "section-holding-only-a-form-feed-is-not-empty",
            _after_context("## Approach\n\nx\n\n## Critical files\n\n\u000c\n\n## Verification\n\nx\n\n## Out of scope\n\nx\n"),
            check=_pass(),
        ),
    ]


def _hostile_cases() -> list[Case]:
    return [
        Case(
            "hostile-heading-lines-in-context",
            _TITLE_AND_CONTEXT + "\n" + _HOSTILE_HEADING_LINES + "\n" + _AUTHORED_REGION,
            _ready("revision"),
            _pass(),
        ),
        Case(
            "hostile-heading-lines-in-region",
            _after_context(_AUTHORED_REGION + _HOSTILE_HEADING_LINES),
            _stop(("foreign-heading", (22, 23, 24, 25))),
            _fail(
                "revision",
                ("headings", (7, 11, 15, 19, 22, 23, 24, 25)),
                ("empty-section", (22,)),
                ("empty-section", (23,)),
                ("empty-section", (24,)),
                ("empty-section", (25,)),
            ),
        ),
        Case(
            "hostile-heading-lines-inside-a-fence-in-region",
            _after_context(
                "## Approach\n\nx\n\n## Critical files\n\nx\n\n## Verification\n\n```\n"
                + _HOSTILE_HEADING_LINES
                + "```\n\n## Out of scope\n\nx\n"
            ),
            _ready("revision"),
            _pass(),
        ),
    ]


def _fence_wiring_cases() -> list[Case]:
    fenced_in_region = _after_context(
        "## Approach\n\nx\n\n## Critical files\n\n```\n## X\n```\n\n## Verification\n\nx\n\n## Out of scope\n\nx\n"
    )
    return [
        Case(
            "fenced-effort-in-context-does-not-block-the-scaffold",
            "# Title\n\n## Context\n\nAsk: x\n\n```\n## Effort\n```\n",
            prepare=_ready("first-authoring", scaffolds=True),
        ),
        Case("fenced-heading-in-region-is-not-foreign-to-prepare", fenced_in_region, prepare=_ready("revision")),
        Case("fenced-heading-in-region-is-not-foreign-to-check", fenced_in_region, check=_pass()),
    ]


_CORPUS: list[Case] = [
    *_byte_faults(),
    *_hash_range_cases(),
    *_line_cases(),
    *_heading_lookalike_cases(),
    *_heading_identity_cases(),
    *_single_heading_cases(),
    *_placeholder_scope_cases(),
    *_prepare_cases(),
    *_check_cases(),
    *_hostile_cases(),
    *_fence_wiring_cases(),
]
_CASE_MODE_PAIRS = [
    pytest.param(case, mode, id=f"{case.name}-{mode}")
    for case in _CORPUS
    for mode in ("prepare", "check")
    if getattr(case, mode) is not None
]
_SED_CASES = [pytest.param(case, id=case.name) for case in _CORPUS if case.sed_last_line is not None]


@pytest.fixture(scope="module")
def script():
    """The script as a module. Loading inside a fixture makes a missing script fail each test, not collection."""
    spec = importlib.util.spec_from_file_location("plan_shape", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass
class ParsedOutput:
    lines: list[str]
    verdict: str | None
    stage: str | None
    reasons: list[tuple[str, tuple[int, ...]]]


@dataclass
class RunResult:
    status: int
    stdout: str
    stderr: str


def _parse_output(stdout: str) -> ParsedOutput:
    """Splits each line on its first `: `, so cases pin codes and numbers, not sentence prose."""
    lines = stdout.removesuffix("\n").split("\n") if stdout else []
    verdict = None
    stage = None
    reasons = []
    for line in lines:
        key, _, value = line.partition(": ")
        if key in ("PREPARE", "CHECK"):
            verdict = value
        elif key == "STAGE":
            stage = value
        elif key == "REASON":
            code, _, sentence = value.partition(": ")
            reasons.append((code, tuple(int(number) for number in re.findall(r"\d+", sentence))))
    return ParsedOutput(lines, verdict, stage, reasons)


def _run_script(*arguments: str) -> RunResult:
    try:
        completed = subprocess.run(
            [sys.executable, str(_SCRIPT_PATH), *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"plan-shape.py {' '.join(arguments)} blocked past {_SUBPROCESS_TIMEOUT_SECONDS}s")
    return RunResult(completed.returncode, completed.stdout, completed.stderr)


def _run_in_process(script, capture: pytest.CaptureFixture[bytes], *arguments: str) -> RunResult:
    status = script.main(list(arguments))
    captured = capture.readouterr()
    return RunResult(status, captured.out.decode("utf-8"), captured.err.decode("utf-8"))


def _sed_hash_range_last_line(plan_path: Path) -> int:
    """The last line `sed` prints for the hash range, which is where Step 5's Context hash ends."""
    # BSD sed's `=` takes one address, so the range goes on a `{...}` block.
    # BSD sed rejects `{=}`, so the `;` before `}` is required.
    completed = subprocess.run(
        ["sed", "-n", f"{_HASH_RANGE_ADDRESS}{{=;}}", str(plan_path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
    return int(completed.stdout.split()[-1])


def _reason_line(stdout: str, code: str) -> str:
    """The one raw `REASON` line for a code, with its sentence."""
    matching = [line for line in stdout.split("\n") if line.startswith(f"REASON: {code}: ")]
    assert len(matching) == 1, f"expected one {code} REASON line in {stdout!r}"
    return matching[0]


class _OsWithOverrides:
    """Stands in for the script's `os` name: overrides named functions and passes every other attribute to the real `os`."""

    def __init__(self, **overrides) -> None:
        self._overrides = overrides

    def __getattr__(self, name: str):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(os, name)


def _write_plan(plans_directory: Path, content: str | bytes, name: str = "plan.md") -> Path:
    """Writes bytes, never text, so line endings survive, and pins an old mtime so any rewrite shows."""
    plans_directory.mkdir(parents=True, exist_ok=True)
    plan_path = plans_directory / name
    plan_path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    os.utime(plan_path, ns=(_OLD_MTIME_NS, _OLD_MTIME_NS))
    return plan_path


def _plans_directory(root: Path) -> Path:
    return root / ".claude" / "plans"


# Each reason code with the names of the script constants holding its sentence templates.
# internal-error is absent because its sentence carries an exception type name.
# usage is absent because usage output has no verdict line and its own test compares raw stdout.
_SENTENCE_CONSTANTS_BY_CODE = {
    "plan-path": ("_SENTENCE_PLAN_PATH",),
    "unreadable": ("_SENTENCE_UNREADABLE_CHECK", "_SENTENCE_UNREADABLE_PREPARE"),
    "not-utf8": ("_SENTENCE_NOT_UTF8",),
    "carriage-return": ("_SENTENCE_CARRIAGE_RETURN",),
    "unclosed-fence": ("_SENTENCE_UNCLOSED_FENCE",),
    "hash-range": ("_SENTENCE_HASH_RANGE",),
    "not-title-and-context": ("_SENTENCE_NOT_TITLE_AND_CONTEXT",),
    "foreign-heading": ("_SENTENCE_FOREIGN_HEADING",),
    "unwritable": ("_SENTENCE_UNWRITABLE",),
    "placeholder": ("_SENTENCE_PLACEHOLDER",),
    "headings": ("_SENTENCE_HEADINGS",),
    "empty-section": ("_SENTENCE_EMPTY_SECTION",),
}


def _sentence_matches_a_template_for_code(script, code: str, sentence: str) -> bool:
    for constant_name in _SENTENCE_CONSTANTS_BY_CODE[code]:
        template = getattr(script, constant_name)
        pattern = re.escape(template).replace(re.escape("{lines}"), r"(?:\d+(?:, \d+)*|none)")
        if re.fullmatch(pattern, sentence):
            return True
    return False


def _assert_output_grammar(script, parsed: ParsedOutput) -> None:
    assert parsed.lines, "plan-shape.py printed nothing"
    line_keys = [line.partition(": ")[0] for line in parsed.lines]
    assert line_keys[0] in ("PREPARE", "CHECK"), f"output does not open with a verdict line: {parsed.lines!r}"
    stage_count = line_keys.count("STAGE")
    assert stage_count <= 1, f"more than one STAGE line: {parsed.lines!r}"
    if stage_count:
        assert line_keys[1] == "STAGE", f"STAGE line is not directly after the verdict: {parsed.lines!r}"
    lines_after_verdict_and_stage = line_keys[1 + stage_count :]
    assert all(key == "REASON" for key in lines_after_verdict_and_stage), (
        f"lines after the verdict and STAGE are not all REASON lines: {parsed.lines!r}"
    )
    for line in parsed.lines:
        if re.fullmatch(r"(PREPARE: (ready|stop)|CHECK: (pass|fail)|STAGE: (first-authoring|revision))", line):
            continue
        key, _, value = line.partition(": ")
        code, _, sentence = value.partition(": ")
        assert key == "REASON", f"unknown output line {line!r}"
        assert code in script._REASON_CODES, f"unknown reason code in {line!r}"
        if code == "internal-error":
            continue
        assert _sentence_matches_a_template_for_code(script, code, sentence), f"not a sentence for {code}: {line!r}"


class TestCorpus:
    @pytest.mark.parametrize(("case", "mode"), _CASE_MODE_PAIRS)
    def test_whole_result_matches_expected(
        self, script, tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes], case: Case, mode: str
    ) -> None:
        expected: Expected = getattr(case, mode)
        plan_path = _write_plan(_plans_directory(tmp_path), case.content)
        original = plan_path.read_bytes()
        mtime_before = plan_path.stat().st_mtime_ns

        result = _run_in_process(script, capsysbinary, mode, str(plan_path))

        parsed = _parse_output(result.stdout)
        assert result.status == 0
        assert result.stderr == ""
        _assert_output_grammar(script, parsed)
        assert parsed.verdict == expected.verdict
        assert parsed.stage == expected.stage
        assert tuple(parsed.reasons) == expected.reasons
        after = plan_path.read_bytes()
        if expected.scaffolds:
            assert after.startswith(original)
            assert after.endswith(_SCAFFOLD.encode("utf-8"))
        else:
            assert after == original
            assert plan_path.stat().st_mtime_ns == mtime_before

    @pytest.mark.parametrize("case", _SED_CASES)
    def test_sed_hash_range_ends_where_the_fixture_says(self, tmp_path: Path, case: Case) -> None:
        """Pins where `sed` ends the hash range for each fixture. The script is not run."""
        plan_path = _write_plan(_plans_directory(tmp_path), case.content)

        assert _sed_hash_range_last_line(plan_path) == case.sed_last_line

    def test_corpus_and_the_five_separate_cases_reach_every_reason_code(self, script) -> None:
        codes_in_corpus = {
            code
            for case in _CORPUS
            for expected in (case.prepare, case.check)
            if expected is not None
            for code, _ in expected.reasons
        }

        # Each code the corpus cannot reach with a byte fixture at a valid path maps to the test that reaches it.
        tests_reaching_codes_outside_the_corpus = {
            "plan-path": TestPath.test_refuses_the_path_and_leaves_the_file_unchanged,
            "unreadable": TestFileType.test_a_missing_file_is_unreadable,
            "unwritable": TestPrepare.test_a_write_that_raises_is_unwritable_and_leaves_the_file_unchanged,
            "internal-error": TestInternalError.test_prints_the_negative_verdict_and_only_the_exception_type_name,
            "usage": TestContract.test_usage_error_exits_2_with_one_usage_line_and_no_stderr,
        }

        assert codes_in_corpus | tests_reaching_codes_outside_the_corpus.keys() == set(script._REASON_CODES)


# Rows: file text, then the expected (fenced line numbers, opener line of a fence still open at the end).
_FENCE_ROWS = {
    "backtick-fence": (["```", "## P", "```", "## P"], ({1, 2, 3}, None)),
    "tilde-fence": (["~~~", "## P", "~~~", "## P"], ({1, 2, 3}, None)),
    "bash-info-string": (["```bash", "## P", "```", "## P"], ({1, 2, 3}, None)),
    "text-info-string": (["```text", "## P", "```", "## P"], ({1, 2, 3}, None)),
    "tilde-opener-with-backtick-in-info-string": (["~~~ a`b", "## P", "~~~", "## P"], ({1, 2, 3}, None)),
    "one-space-indent": ([" ```", "## P", " ```", "## P"], ({1, 2, 3}, None)),
    "two-space-indent": (["  ```", "## P", "  ```", "## P"], ({1, 2, 3}, None)),
    "three-space-indent": (["   ```", "## P", "   ```", "## P"], ({1, 2, 3}, None)),
    "fence-opened-after-prose": (["x", "```", "y", "```"], ({2, 3, 4}, None)),
    "longer-outer-fence-around-shorter-inner-opener": (
        ["````", "```", "## P", "```", "````", "## P"],
        ({1, 2, 3, 4, 5}, None),
    ),
    "closer-with-trailing-spaces-and-tabs": (["```", "## P", "``` \t ", "## P"], ({1, 2, 3}, None)),
    "longer-closer": (["```", "## P", "`````", "## P"], ({1, 2, 3}, None)),
    "four-space-opener-is-not-a-fence": (["    ```", "## P", "    ```", "## P"], (set(), None)),
    "list-marker-backtick-opener-is-not-a-fence-and-its-closer-opens-one": (
        ["- ```bash", "## P", "  ```", "## P"],
        ({3, 4}, 3),
    ),
    "ordered-list-marker-tilde-opener-is-not-a-fence-and-its-closer-opens-one": (
        ["1. ~~~", "## P", "   ~~~", "## P"],
        ({3, 4}, 3),
    ),
    "block-quoted-opener-is-not-a-fence": (["> ```", "## P", "> ```", "## P"], (set(), None)),
    "backtick-in-backtick-info-string-is-not-an-opener": (["``` a`b", "## P"], (set(), None)),
    "two-backtick-run-is-not-an-opener": (["``", "## P"], (set(), None)),
    "two-tilde-run-is-not-an-opener": (["~~", "## P"], (set(), None)),
    "tab-indented-opener-is-not-a-fence": (["\t```", "## P"], (set(), None)),
    "second-fence-left-open-after-the-first-closes": (["```", "x", "```", "y", "~~~", "## P"], ({1, 2, 3, 5, 6}, 5)),
    "shorter-closer-does-not-close": (["````", "## P", "```", "## P"], ({1, 2, 3, 4}, 1)),
    "other-character-closer-does-not-close": (["```", "## P", "~~~", "## P"], ({1, 2, 3, 4}, 1)),
    "closer-with-trailing-text-does-not-close": (["```", "## P", "``` x", "## P"], ({1, 2, 3, 4}, 1)),
    "four-space-closer-does-not-close": (["```", "## P", "    ```", "## P"], ({1, 2, 3, 4}, 1)),
    "tab-indented-closer-does-not-close": (["```", "## P", "\t```", "## P"], ({1, 2, 3, 4}, 1)),
}


class TestFenceScan:
    @pytest.mark.parametrize(("lines", "expected"), _FENCE_ROWS.values(), ids=_FENCE_ROWS.keys())
    def test_returns_the_fenced_lines_and_the_unclosed_opener(self, script, lines, expected) -> None:
        scan = script._scan_fences(lines)

        assert (set(scan.fenced_line_numbers), scan.unclosed_opener_line) == expected


_PATH_REFUSAL_BUILDERS = {}


def _path_refusal(builder):
    _PATH_REFUSAL_BUILDERS[builder.__name__.removeprefix("_build_")] = builder
    return builder


@_path_refusal
def _build_plans_directory_without_a_claude_parent(root: Path):
    plan_path = _write_plan(root / "plans", _TITLE_AND_CONTEXT, "x.md")
    return str(plan_path), plan_path


@_path_refusal
def _build_claude_directory_that_is_not_plans(root: Path):
    plan_path = _write_plan(root / ".claude" / "rules", _TITLE_AND_CONTEXT, "x.md")
    return str(plan_path), plan_path


@_path_refusal
def _build_name_ending_in_a_newline(root: Path):
    plans = _plans_directory(root)
    plan_path = _write_plan(plans, _TITLE_AND_CONTEXT, "x.md\n")
    return str(plans) + "/x.md\n", plan_path


@_path_refusal
def _build_dot_segment(root: Path):
    plan_path = _write_plan(_plans_directory(root), _TITLE_AND_CONTEXT, "x.md")
    return str(plan_path.parent) + "/./x.md", plan_path


@_path_refusal
def _build_dot_dot_segment(root: Path):
    plan_path = _write_plan(_plans_directory(root), _TITLE_AND_CONTEXT, "x.md")
    return str(plan_path.parent) + "/../plans/x.md", plan_path


@_path_refusal
def _build_empty_segment(root: Path):
    plan_path = _write_plan(_plans_directory(root), _TITLE_AND_CONTEXT, "x.md")
    return str(plan_path.parent) + "//x.md", plan_path


@_path_refusal
def _build_trailing_slash(root: Path):
    plan_path = _write_plan(_plans_directory(root), _TITLE_AND_CONTEXT, "x.md")
    return str(plan_path) + "/", plan_path


@_path_refusal
def _build_name_with_a_space(root: Path):
    plan_path = _write_plan(_plans_directory(root), _TITLE_AND_CONTEXT, "a b.md")
    return str(plan_path), plan_path


@_path_refusal
def _build_name_with_a_quote(root: Path):
    plan_path = _write_plan(_plans_directory(root), _TITLE_AND_CONTEXT, "a'b.md")
    return str(plan_path), plan_path


@_path_refusal
def _build_name_with_a_leading_dot(root: Path):
    plan_path = _write_plan(_plans_directory(root), _TITLE_AND_CONTEXT, ".x.md")
    return str(plan_path), plan_path


@_path_refusal
def _build_name_with_a_leading_hyphen(root: Path):
    plan_path = _write_plan(_plans_directory(root), _TITLE_AND_CONTEXT, "-x.md")
    return str(plan_path), plan_path


@_path_refusal
def _build_name_with_a_leading_underscore(root: Path):
    plan_path = _write_plan(_plans_directory(root), _TITLE_AND_CONTEXT, "_x.md")
    return str(plan_path), plan_path


@_path_refusal
def _build_name_with_a_non_ascii_letter(root: Path):
    plan_path = _write_plan(_plans_directory(root), _TITLE_AND_CONTEXT, "\u00e9.md")
    return str(plan_path), plan_path


@_path_refusal
def _build_name_with_a_non_ascii_letter_after_the_first_character(root: Path):
    plan_path = _write_plan(_plans_directory(root), _TITLE_AND_CONTEXT, "a\u00e9.md")
    return str(plan_path), plan_path


@_path_refusal
def _build_name_not_ending_in_md(root: Path):
    plan_path = _write_plan(_plans_directory(root), _TITLE_AND_CONTEXT, "x.txt")
    return str(plan_path), plan_path


@_path_refusal
def _build_name_with_an_uppercase_suffix(root: Path):
    plan_path = _write_plan(_plans_directory(root), _TITLE_AND_CONTEXT, "x.MD")
    return str(plan_path), plan_path


@_path_refusal
def _build_parent_symlink_resolving_outside_a_claude_plans_directory(root: Path):
    elsewhere_plan = _write_plan(root / "elsewhere", _TITLE_AND_CONTEXT, "x.md")
    plans = _plans_directory(root)
    plans.parent.mkdir(parents=True)
    plans.symlink_to(root / "elsewhere")
    return str(plans / "x.md"), elsewhere_plan


@_path_refusal
def _build_claude_plans_symlink_to_a_plans_directory_not_under_a_claude_directory(root: Path):
    elsewhere_plan = _write_plan(root / "elsewhere" / "plans", _TITLE_AND_CONTEXT, "x.md")
    plans = _plans_directory(root)
    plans.parent.mkdir(parents=True)
    plans.symlink_to(root / "elsewhere" / "plans")
    return str(plans / "x.md"), elsewhere_plan


@_path_refusal
def _build_claude_directory_symlinked_to_a_differently_named_directory(root: Path):
    # The name tests run on the resolved parent, so the link's own `.claude` name does not help.
    dotfiles_plan = _write_plan(root / "dotfiles" / "plans", _TITLE_AND_CONTEXT, "x.md")
    (root / ".claude").symlink_to(root / "dotfiles")
    return str(_plans_directory(root) / "x.md"), dotfiles_plan


@_path_refusal
def _build_plans_directory_that_does_not_exist(root: Path):
    elsewhere_plan = _write_plan(root / "elsewhere", _TITLE_AND_CONTEXT, "x.md")
    return str(_plans_directory(root) / "x.md"), elsewhere_plan


class TestPath:
    @pytest.mark.parametrize("mode", ["prepare", "check"])
    @pytest.mark.parametrize("builder", _PATH_REFUSAL_BUILDERS.values(), ids=_PATH_REFUSAL_BUILDERS.keys())
    def test_refuses_the_path_and_leaves_the_file_unchanged(
        self, script, tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes], builder, mode: str
    ) -> None:
        path_argument, existing_file = builder(tmp_path)
        original = existing_file.read_bytes()
        mtime_before = existing_file.stat().st_mtime_ns

        result = _run_in_process(script, capsysbinary, mode, path_argument)

        parsed = _parse_output(result.stdout)
        assert result.status == 0
        assert result.stderr == ""
        _assert_output_grammar(script, parsed)
        assert parsed.verdict == ("stop" if mode == "prepare" else "fail")
        assert parsed.stage is None
        assert parsed.reasons == [("plan-path", ())]
        assert existing_file.read_bytes() == original
        assert existing_file.stat().st_mtime_ns == mtime_before

    @pytest.mark.parametrize("mode", ["prepare", "check"])
    def test_refuses_a_relative_path_that_resolves_to_a_claude_plans_file_from_the_working_directory(
        self,
        script,
        tmp_path: Path,
        capsysbinary: pytest.CaptureFixture[bytes],
        monkeypatch: pytest.MonkeyPatch,
        mode: str,
    ) -> None:
        plan_path = _write_plan(_plans_directory(tmp_path), _TITLE_AND_CONTEXT, "x.md")
        original = plan_path.read_bytes()
        mtime_before = plan_path.stat().st_mtime_ns
        monkeypatch.chdir(tmp_path)

        result = _run_in_process(script, capsysbinary, mode, ".claude/plans/x.md")

        parsed = _parse_output(result.stdout)
        assert result.status == 0
        assert result.stderr == ""
        _assert_output_grammar(script, parsed)
        assert parsed.verdict == ("stop" if mode == "prepare" else "fail")
        assert parsed.stage is None
        assert parsed.reasons == [("plan-path", ())]
        assert plan_path.read_bytes() == original
        assert plan_path.stat().st_mtime_ns == mtime_before

    def test_accepts_a_parent_symlink_resolving_to_a_claude_plans_directory(self, tmp_path: Path) -> None:
        real_plan = _write_plan(_plans_directory(tmp_path), _TITLE_AND_CONTEXT, "x.md")
        link = tmp_path / "link"
        link.symlink_to(real_plan.parent)

        result = _run_script("prepare", str(link / "x.md"))

        assert _parse_output(result.stdout).verdict == "ready"
        assert real_plan.read_bytes().endswith(_SCAFFOLD.encode("utf-8"))

    @pytest.mark.parametrize("mode", ["prepare", "check"])
    @pytest.mark.parametrize(
        "plan_name",
        ["a.b.md", "a_b.md", "a-b.md", "A.md", "0.md", "aZ.md", "a9.md"],
        ids=[
            "dot-after-first-character",
            "underscore-after-first-character",
            "hyphen-after-first-character",
            "uppercase-first-character",
            "digit-first-character",
            "uppercase-after-first-character",
            "digit-after-first-character",
        ],
    )
    def test_accepts_a_plan_file_name_within_the_allowed_character_set(
        self, script, tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes], plan_name: str, mode: str
    ) -> None:
        plan_path = _write_plan(_plans_directory(tmp_path), _after_context(_AUTHORED_REGION), plan_name)
        original = plan_path.read_bytes()

        result = _run_in_process(script, capsysbinary, mode, str(plan_path))

        parsed = _parse_output(result.stdout)
        assert result.status == 0
        assert result.stderr == ""
        _assert_output_grammar(script, parsed)
        assert parsed.verdict == ("ready" if mode == "prepare" else "pass")
        assert parsed.reasons == []
        assert plan_path.read_bytes() == original


def _assert_unreadable_sentence_names_the_mode_access(stdout: str, mode: str) -> None:
    sentence = _reason_line(stdout, "unreadable")
    if mode == "prepare":
        assert "reading and appending" in sentence
    else:
        assert "reading" in sentence
        assert "appending" not in sentence


class TestFileType:
    @pytest.mark.parametrize("mode", ["prepare", "check"])
    def test_a_missing_file_is_unreadable(self, script, tmp_path: Path, mode: str) -> None:
        plans = _plans_directory(tmp_path)
        plans.mkdir(parents=True)

        result = _run_script(mode, str(plans / "missing.md"))

        parsed = _parse_output(result.stdout)
        _assert_output_grammar(script, parsed)
        assert parsed.reasons == [("unreadable", ())]
        assert parsed.stage is None
        _assert_unreadable_sentence_names_the_mode_access(result.stdout, mode)

    @pytest.mark.parametrize("mode", ["prepare", "check"])
    def test_a_directory_is_unreadable(self, script, tmp_path: Path, mode: str) -> None:
        directory = _plans_directory(tmp_path) / "x.md"
        directory.mkdir(parents=True)

        result = _run_script(mode, str(directory))

        parsed = _parse_output(result.stdout)
        _assert_output_grammar(script, parsed)
        assert parsed.reasons == [("unreadable", ())]
        _assert_unreadable_sentence_names_the_mode_access(result.stdout, mode)

    @pytest.mark.parametrize("mode", ["prepare", "check"])
    def test_a_symlink_at_the_last_component_is_unreadable_and_its_target_unchanged(
        self, script, tmp_path: Path, mode: str
    ) -> None:
        target = _write_plan(tmp_path / "elsewhere", _TITLE_AND_CONTEXT, "target.md")
        plans = _plans_directory(tmp_path)
        plans.mkdir(parents=True)
        (plans / "x.md").symlink_to(target)
        mtime_before = target.stat().st_mtime_ns

        result = _run_script(mode, str(plans / "x.md"))

        parsed = _parse_output(result.stdout)
        _assert_output_grammar(script, parsed)
        assert parsed.reasons == [("unreadable", ())]
        _assert_unreadable_sentence_names_the_mode_access(result.stdout, mode)
        assert target.read_bytes() == _TITLE_AND_CONTEXT.encode("utf-8")
        assert target.stat().st_mtime_ns == mtime_before

    @pytest.mark.parametrize("mode", ["prepare", "check"])
    def test_a_fifo_is_unreadable_without_blocking(self, script, tmp_path: Path, mode: str) -> None:
        """A read-only open of a FIFO with no writer blocks without O_NONBLOCK, which `check` detects.
        A read-write open does not block on Linux, so `prepare` is held by the regular-file test."""
        plans = _plans_directory(tmp_path)
        plans.mkdir(parents=True)
        os.mkfifo(plans / "x.md")

        result = _run_script(mode, str(plans / "x.md"))

        parsed = _parse_output(result.stdout)
        _assert_output_grammar(script, parsed)
        assert parsed.reasons == [("unreadable", ())]
        _assert_unreadable_sentence_names_the_mode_access(result.stdout, mode)

    @pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-0444 file for writing")
    def test_a_read_only_file_passes_check_and_is_unreadable_to_prepare_and_unchanged(self, script, tmp_path: Path) -> None:
        plan_path = _write_plan(_plans_directory(tmp_path), _after_context(_AUTHORED_REGION))
        plan_path.chmod(0o444)
        original = plan_path.read_bytes()
        mtime_before = plan_path.stat().st_mtime_ns

        check = _run_script("check", str(plan_path))
        prepare = _run_script("prepare", str(plan_path))

        check_parsed = _parse_output(check.stdout)
        prepare_parsed = _parse_output(prepare.stdout)
        _assert_output_grammar(script, check_parsed)
        _assert_output_grammar(script, prepare_parsed)
        assert check_parsed.verdict == "pass"
        assert prepare_parsed.reasons == [("unreadable", ())]
        _assert_unreadable_sentence_names_the_mode_access(prepare.stdout, "prepare")
        assert plan_path.read_bytes() == original
        assert plan_path.stat().st_mtime_ns == mtime_before

    @pytest.mark.parametrize("mode", ["prepare", "check"])
    def test_a_read_that_raises_is_unreadable_not_an_internal_error(
        self, script, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes], mode: str
    ) -> None:
        plan_path = _write_plan(_plans_directory(tmp_path), _TITLE_AND_CONTEXT)

        def raising_read(descriptor: int, length: int) -> bytes:
            raise OSError("input/output error")

        # Patch the script module's own `os` name, so no other `os.read` caller in the process is hit.
        monkeypatch.setattr(script, "os", _OsWithOverrides(read=raising_read))
        result = _run_in_process(script, capsysbinary, mode, str(plan_path))

        parsed = _parse_output(result.stdout)
        assert result.status == 0
        _assert_output_grammar(script, parsed)
        assert parsed.reasons == [("unreadable", ())]
        _assert_unreadable_sentence_names_the_mode_access(result.stdout, mode)
        assert plan_path.read_bytes() == _TITLE_AND_CONTEXT.encode("utf-8")


_CONTEXT_OPENING = "# Title\n\n## Context\n\nAsk: "


def _plan_with_context_filled_to_byte(byte_count: int, tail: str = "") -> bytes:
    """A valid authored plan whose Context is padded with ASCII filler so that `tail` starts at file byte
    `byte_count`, then the four headings."""
    filler = "a" * (byte_count - len(_CONTEXT_OPENING))
    return (_CONTEXT_OPENING + filler + tail + "\n\n" + _AUTHORED_REGION).encode("utf-8")


def _plan_with_headings_after_the_first_read_chunk(chunk_bytes: int) -> bytes:
    plan = _plan_with_context_filled_to_byte(chunk_bytes + 100)
    assert plan.index(b"## Approach") > chunk_bytes
    return plan


def _plan_with_a_multibyte_character_across_the_read_chunk_boundary(chunk_bytes: int) -> bytes:
    plan = _plan_with_context_filled_to_byte(chunk_bytes - 1, tail="\u00e9")
    assert plan.index("\u00e9".encode()) == chunk_bytes - 1
    return plan


class TestLargeFile:
    @pytest.mark.parametrize("mode", ["prepare", "check"])
    @pytest.mark.parametrize(
        "build_plan",
        [
            _plan_with_headings_after_the_first_read_chunk,
            _plan_with_a_multibyte_character_across_the_read_chunk_boundary,
        ],
        ids=["headings-after-the-first-chunk", "multibyte-character-across-the-chunk-boundary"],
    )
    def test_reads_past_one_chunk_passes_and_leaves_the_bytes_unchanged(
        self, script, tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes], build_plan, mode: str
    ) -> None:
        plan_path = _write_plan(_plans_directory(tmp_path), build_plan(script._READ_CHUNK_BYTES))
        original = plan_path.read_bytes()
        mtime_before = plan_path.stat().st_mtime_ns

        result = _run_in_process(script, capsysbinary, mode, str(plan_path))

        parsed = _parse_output(result.stdout)
        assert result.status == 0
        _assert_output_grammar(script, parsed)
        assert parsed.verdict == ("ready" if mode == "prepare" else "pass")
        assert parsed.stage == "revision"
        assert parsed.reasons == []
        assert plan_path.read_bytes() == original
        assert plan_path.stat().st_mtime_ns == mtime_before


class TestPrepare:
    @pytest.mark.parametrize(
        ("ending", "padding"),
        [("", "\n\n"), ("\n", "\n"), ("\n\n", ""), ("\n\n\n", "")],
        ids=["no-newline", "one-newline", "two-newlines", "three-newlines"],
    )
    def test_appends_the_scaffold_after_the_padding_and_changes_no_existing_byte(
        self, tmp_path: Path, ending: str, padding: str
    ) -> None:
        original = "# Title\n\n## Context\n\nAsk: x" + ending
        plan_path = _write_plan(_plans_directory(tmp_path), original)

        _run_script("prepare", str(plan_path))

        assert plan_path.read_bytes() == (original + padding + _SCAFFOLD).encode("utf-8")

    def test_a_second_run_changes_nothing_and_prints_the_same_lines(self, tmp_path: Path) -> None:
        plan_path = _write_plan(_plans_directory(tmp_path), _TITLE_AND_CONTEXT)
        first = _run_script("prepare", str(plan_path))
        bytes_after_first_run = plan_path.read_bytes()

        second = _run_script("prepare", str(plan_path))

        assert plan_path.read_bytes() == bytes_after_first_run
        assert first.stdout == second.stdout
        assert second.stdout == "PREPARE: ready\nSTAGE: first-authoring\n"

    def test_prepare_then_check_leaves_the_placeholder_and_three_empty_sections(self, tmp_path: Path) -> None:
        plan_path = _write_plan(_plans_directory(tmp_path), _TITLE_AND_CONTEXT)

        _run_script("prepare", str(plan_path))
        parsed = _parse_output(_run_script("check", str(plan_path)).stdout)
        hash_range_last_line = _sed_hash_range_last_line(plan_path)

        assert parsed.verdict == "fail"
        assert parsed.stage == "first-authoring"
        assert parsed.reasons == [
            ("placeholder", ()),
            ("empty-section", (11,)),
            ("empty-section", (13,)),
            ("empty-section", (15,)),
        ]
        assert hash_range_last_line == 7

    def test_a_write_that_raises_is_unwritable_and_leaves_the_file_unchanged(
        self, script, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]
    ) -> None:
        plan_path = _write_plan(_plans_directory(tmp_path), _TITLE_AND_CONTEXT)

        def raising_write(descriptor: int, data: bytes) -> int:
            raise OSError("disk full")

        # Patch the script module's own `os` name, so no other `os.write` caller in the process is hit.
        monkeypatch.setattr(script, "os", _OsWithOverrides(write=raising_write))
        status = script.main(["prepare", str(plan_path)])

        stdout = capsysbinary.readouterr().out.decode("utf-8")
        parsed = _parse_output(stdout)
        assert status == 0
        _assert_output_grammar(script, parsed)
        assert parsed.verdict == "stop"
        assert parsed.stage is None
        assert parsed.reasons == [("unwritable", ())]
        assert "the plan file may now hold part of the scaffold" in _reason_line(stdout, "unwritable")
        assert plan_path.read_bytes() == _TITLE_AND_CONTEXT.encode("utf-8")

    def test_a_short_write_is_unwritable_and_the_file_holds_part_of_the_scaffold(
        self, script, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]
    ) -> None:
        plan_path = _write_plan(_plans_directory(tmp_path), _TITLE_AND_CONTEXT)
        real_write = os.write

        def short_write(descriptor: int, data: bytes) -> int:
            written_count = len(data) // 2
            return real_write(descriptor, data[:written_count])

        # Patch the script module's own `os` name, so no other `os.write` caller in the process is hit.
        monkeypatch.setattr(script, "os", _OsWithOverrides(write=short_write))
        status = script.main(["prepare", str(plan_path)])

        stdout = capsysbinary.readouterr().out.decode("utf-8")
        parsed = _parse_output(stdout)
        full_payload = ("\n" + _SCAFFOLD).encode("utf-8")
        appended = plan_path.read_bytes().removeprefix(_TITLE_AND_CONTEXT.encode("utf-8"))
        assert status == 0
        _assert_output_grammar(script, parsed)
        assert parsed.verdict == "stop"
        assert parsed.reasons == [("unwritable", ())]
        assert "the plan file may now hold part of the scaffold" in _reason_line(stdout, "unwritable")
        assert 0 < len(appended) < len(full_payload)
        assert full_payload.startswith(appended)


class TestInternalError:
    @pytest.mark.parametrize(
        ("mode", "expected_lines"),
        [
            ("check", ["CHECK: fail", "REASON: internal-error: RuntimeError"]),
            (
                "prepare",
                ["PREPARE: stop", "REASON: internal-error: RuntimeError" + _INTERNAL_ERROR_PREPARE_SUFFIX],
            ),
        ],
    )
    def test_prints_the_negative_verdict_and_only_the_exception_type_name(
        self, script, monkeypatch: pytest.MonkeyPatch, capsysbinary: pytest.CaptureFixture[bytes], mode: str, expected_lines
    ) -> None:
        def raising_evaluate(mode: str, plan_argument: str):
            raise RuntimeError("secret detail in " + plan_argument)

        monkeypatch.setattr(script, "_evaluate", raising_evaluate)

        status = script.main([mode, "/plan/path.md"])

        captured = capsysbinary.readouterr()
        assert status == 1
        assert captured.out.decode("utf-8").splitlines() == expected_lines
        assert captured.err == b""


class TestContract:
    def test_docstring_keys_line_equals_the_key_constant(self, script) -> None:
        match = re.search(r"^Keys: ([A-Z, ]+)$", script.__doc__, flags=re.MULTILINE)

        assert match is not None
        assert tuple(re.split(r",\s*", match.group(1))) == script._KEYS

    def test_docstring_reason_codes_line_equals_the_code_constant(self, script) -> None:
        match = re.search(r"^Reason codes: ([a-z0-9, \n-]+)\n\n", script.__doc__, flags=re.MULTILINE)

        assert match is not None
        assert tuple(re.split(r",\s*", " ".join(match.group(1).split()))) == script._REASON_CODES

    @pytest.mark.parametrize(
        "arguments",
        [(), ("check", "/a", "/b"), ("unknown", "/a"), ("-h",), ("--help",), ("check",)],
        ids=["no-argument", "three-arguments", "unknown-mode", "dash-h", "dash-dash-help", "missing-path"],
    )
    def test_usage_error_exits_2_with_one_usage_line_and_no_stderr(self, arguments: tuple[str, ...]) -> None:
        result = _run_script(*arguments)

        assert result.status == 2
        assert result.stdout == _USAGE_LINE + "\n"
        assert result.stderr == ""

    def test_the_script_is_executable(self) -> None:
        assert os.access(_SCRIPT_PATH, os.X_OK)


class TestPythonFloor:
    def test_script_parses_under_python_311_grammar(self) -> None:
        """Covers 3.11 grammar except PEP 701 f-strings, which parse accepts."""
        ast.parse(_SCRIPT_PATH.read_text(), feature_version=(3, 11))

    @pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 701 f-strings only parse on 3.12+")
    def test_script_has_no_fstring_that_only_python_312_accepts(self) -> None:
        """Covers the PEP 701 f-string forms that feature_version=(3, 11) accepts."""
        assert _fstring_offenders('f"{d["k"]}"') != [], "the check must flag a nested string literal"

        assert _fstring_offenders(_SCRIPT_PATH.read_text()) == []


_STEP5_START = "\n## Step 5 — Architecture design\n"
_STEP6_START = "\n## Step 6"


def _step5_section() -> str:
    text = _SKILL_MD.read_text()
    assert _STEP5_START in text, f"plan-it/SKILL.md no longer has the {_STEP5_START.strip()!r} heading"
    after_heading = text.split(_STEP5_START, 1)[1]
    assert _STEP6_START in after_heading, (
        f"plan-it/SKILL.md has no {_STEP6_START.strip()!r} heading after Step 5 to bound it"
    )
    return after_heading.split(_STEP6_START, 1)[0]


# test_skills.py pins the check command inside Step 5's clauses.
class TestSkillContract:
    def test_step5_names_every_output_key(self, script) -> None:
        section = _step5_section()

        missing = [key for key in script._KEYS if f"`{key}" not in section]

        assert missing == [], f"plan-it/SKILL.md Step 5 does not name {missing}"

    def test_step5_names_the_placeholder_in_backticks(self, script) -> None:
        assert f"`{script._PLACEHOLDER}`" in _step5_section()

    def test_step5_names_the_plan_path_code(self, script) -> None:
        assert "plan-path" in script._REASON_CODES
        assert "`plan-path`" in _step5_section()

    def test_step5_names_exactly_the_redispatch_codes_together(self, script) -> None:
        section = " ".join(_step5_section().split())
        sentence = re.search(r"every `REASON` code is ((?:`[a-z-]+`(?:, | or )?)+)", section)

        assert sentence is not None, "plan-it/SKILL.md Step 5 no longer lists the codes that re-dispatch"
        assert set(re.findall(r"`([a-z-]+)`", sentence.group(1))) == script._REDISPATCH_CODES

    def test_step5_names_the_context_heading_and_the_four_headings(self, script) -> None:
        section = _step5_section()
        quoted = [f"`{heading}`" for heading in script._FOUR_HEADINGS]
        four_heading_phrase = ", ".join(quoted[:-1]) + ", and " + quoted[-1]

        assert f"`{script._CONTEXT_HEADING}`" in section
        assert four_heading_phrase in section

    def test_step5_names_the_verdict_lines_and_first_authoring_stage_in_backticks(self, script) -> None:
        section = _step5_section()
        literals = [
            script._verdict_line(script._MODE_PREPARE, True),
            script._verdict_line(script._MODE_CHECK, True),
            script._line("STAGE", script._STAGE_FIRST_AUTHORING),
        ]

        missing = [literal for literal in literals if f"`{literal}`" not in section]

        assert missing == [], f"plan-it/SKILL.md Step 5 does not name {missing} in backticks"

    def test_step5_gives_the_prepare_command(self) -> None:
        assert "python3 ~/.claude/scripts/plan-shape.py prepare '<plan path>'" in _step5_section()

    def test_step5_keeps_the_hash_range_the_differential_test_runs(self) -> None:
        assert f"sed -n '{_HASH_RANGE_ADDRESS}p'" in _step5_section()
