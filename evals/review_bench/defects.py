"""Defect-set schema for A-bench: `Candidate` and `ConfirmedDefect` records,
their validation, JSON load/save, and the description-provenance check
`confirm` (evals/run_review_bench.py) runs before promoting a candidate into
the committed evals/review_bench/defects.json. `Candidate` records themselves
come from mine_szz.py and mine_review_rounds.py.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

# The seven reviewer lenses whose agent body carries a read clause -- the
# only lenses A-bench's arm 2 (function-context) can substitute a clause
# for. staff-data-engineer and staff-product-engineer own other code-review
# checklist items but have no read clause to replace, so a defect either of
# them owns can never enter this benchmark.
KNOWN_LENSES: frozenset[str] = frozenset({
    "staff-backend-engineer",
    "staff-frontend-engineer",
    "staff-sdet",
    "staff-platform-engineer",
    "staff-analytics-engineer",
    "ciso-reviewer",
    "comment-discipline-reviewer",
})

KNOWN_SOURCES: frozenset[str] = frozenset({"szz", "review-round"})

# Source 2, step 6's four possible outcomes for resolving a review round's
# branch to a reachable ref.
KNOWN_REF_STATUSES: frozenset[str] = frozenset({"local-branch", "fetched", "fetch-failed", "pr-unknown"})

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _validate_sha(field_name: str, value: str) -> None:
    if not _SHA_RE.match(value):
        raise ValueError(f"{field_name} must be a 40-hex commit SHA, got {value!r}")


def _validate_lens(lens: str) -> None:
    if lens not in KNOWN_LENSES:
        raise ValueError(f"lens {lens!r} is not in the known lens set {sorted(KNOWN_LENSES)}")


def _validate_source(source: str) -> None:
    if source not in KNOWN_SOURCES:
        raise ValueError(f"source {source!r} must be one of {sorted(KNOWN_SOURCES)}")


def _validate_fix_date(fix_date: str) -> None:
    try:
        datetime.fromisoformat(fix_date)
    except ValueError as exc:
        raise ValueError(f"fix_date {fix_date!r} is not a valid ISO 8601 date/datetime") from exc


def _validate_common(
    *, lens: str, source: str, base_commit: str, head_commit: str, fix_commit: str, fix_date: str,
) -> None:
    _validate_lens(lens)
    _validate_source(source)
    _validate_sha("base_commit", base_commit)
    _validate_sha("head_commit", head_commit)
    _validate_sha("fix_commit", fix_commit)
    _validate_fix_date(fix_date)


@dataclass(frozen=True)
class Candidate:
    """One miner-produced, pre-confirmation shortlist entry.

    Written to and read from the gitignored evals/review_bench/.local/
    shortlists -- never committed. `excerpt` and `evidence` may carry real
    session or finding text and must never be copied verbatim into a
    ConfirmedDefect's `description` (see `check_description_provenance`).

    `lines_exist_at_introducing_head`, `reviewer_could_have_caught_it`, and
    `file_is_markdown` are the miner's pre-filled guesses for three of the
    plan's four inclusion fields; `lens` doubles as the pre-filled guess for
    the fourth (the owning lens). The engineer confirms or overrides each
    one before promotion.
    """

    id: str
    source: str
    lens: str
    base_commit: str
    head_commit: str
    fix_commit: str
    fix_date: str
    lines_exist_at_introducing_head: bool
    reviewer_could_have_caught_it: bool
    file_is_markdown: bool
    ref_status: str | None = None
    description: str = ""
    excerpt: str = ""
    evidence: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_common(
            lens=self.lens, source=self.source, base_commit=self.base_commit,
            head_commit=self.head_commit, fix_commit=self.fix_commit, fix_date=self.fix_date,
        )
        if self.ref_status is not None and self.ref_status not in KNOWN_REF_STATUSES:
            raise ValueError(f"ref_status {self.ref_status!r} must be one of {sorted(KNOWN_REF_STATUSES)} or None")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping) -> Candidate:
        return cls(**dict(data))


# ConfirmedDefect's exact field set -- no field outside the schema, so
# description is the only free text field.
_CONFIRMED_DEFECT_FIELDS: frozenset[str] = frozenset({
    "id", "source", "lens", "base_commit", "head_commit", "fix_commit", "fix_date", "description",
})


@dataclass(frozen=True)
class ConfirmedDefect:
    """One engineer-confirmed defect, committed to evals/review_bench/defects.json.

    `description` is the only free-text field. `confirm` runs it through
    `check_description_provenance`, which catches a verbatim or
    near-verbatim word run shared with a `.local/` finding excerpt --
    see that function's own docstring for the check's limits.
    """

    id: str
    source: str
    lens: str
    base_commit: str
    head_commit: str
    fix_commit: str
    fix_date: str
    description: str

    def __post_init__(self) -> None:
        _validate_common(
            lens=self.lens, source=self.source, base_commit=self.base_commit,
            head_commit=self.head_commit, fix_commit=self.fix_commit, fix_date=self.fix_date,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping) -> ConfirmedDefect:
        extra = set(data) - _CONFIRMED_DEFECT_FIELDS
        if extra:
            raise ValueError(f"ConfirmedDefect has field(s) outside the schema: {sorted(extra)}")
        missing = _CONFIRMED_DEFECT_FIELDS - set(data)
        if missing:
            raise ValueError(f"ConfirmedDefect is missing field(s): {sorted(missing)}")
        return cls(**dict(data))


def _atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` via a same-directory temp file plus
    `os.replace`, so a crash mid-write or an overlapping writer leaves the
    previous complete file in place rather than a truncated one.
    `os.replace` is atomic on POSIX, which covers `install.sh`'s two
    supported platforms (Linux/macOS)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp_path.write_text(text)
    os.replace(tmp_path, path)


def assert_unique_ids(candidates: list[Candidate], *, miner: str) -> None:
    """Fail loudly on a same-run `Candidate.id` collision, rather than let
    it resolve silently to "last write wins". `confirm`'s `excerpts_by_id`
    and `existing_ids` lookups both key on `id` -- a collision there can
    check a description against the wrong sibling's excerpt, or make a
    distinct finding permanently unconfirmable."""
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.id in seen:
            raise ValueError(
                f"{miner}: duplicate candidate id {candidate.id!r} -- ids must be unique per mined observation"
            )
        seen.add(candidate.id)


def load_candidates(path: Path) -> list[Candidate]:
    if not path.exists():
        return []
    return [Candidate.from_dict(d) for d in json.loads(path.read_text())]


def save_candidates(path: Path, candidates: list[Candidate]) -> None:
    _atomic_write_text(path, json.dumps([c.to_dict() for c in candidates], indent=2, sort_keys=True) + "\n")


def load_confirmed_defects(path: Path) -> list[ConfirmedDefect]:
    if not path.exists():
        return []
    return [ConfirmedDefect.from_dict(d) for d in json.loads(path.read_text())]


def save_confirmed_defects(path: Path, defects: list[ConfirmedDefect]) -> None:
    _atomic_write_text(path, json.dumps([d.to_dict() for d in defects], indent=2, sort_keys=True) + "\n")


def guess_lens(path: str) -> str:
    """A miner's pre-fill guess for a candidate's owning lens.

    Heuristic only, based on the changed path's shape -- real routing is
    judgment over code-review/SKILL.md's Item-ownership table, which this
    function does not read. The engineer confirms or overrides this value
    for every candidate before it can be promoted.
    """
    name = Path(path).name
    if path.endswith(".sh") or "/hooks/" in path:
        return "staff-platform-engineer"
    if name.startswith("test_") or "/tests/" in path:
        return "staff-sdet"
    lowered = path.lower()
    if any(token in lowered for token in ("permission", "credential", "denial", "auth")):
        return "ciso-reviewer"
    if path.endswith(".md"):
        return "comment-discipline-reviewer"
    return "staff-backend-engineer"


# --- Description provenance -------------------------------------------------

# \w already matches Unicode letters (accented Latin included) under a str
# pattern, so no separate non-ASCII handling is needed here.
_TOKEN_RE = re.compile(r"\w+")
_SIX_GRAM_WINDOW = 6


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _n_grams(tokens: list[str], n: int) -> list[str]:
    if n <= 0 or len(tokens) < n:
        return []
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


class ProvenanceViolation(NamedTuple):
    """One `check_description_provenance` rejection: the exact word run
    shared with a `.local/` excerpt (up to `_SIX_GRAM_WINDOW` words, capped
    below that by whichever of the description/excerpt is shorter), and the
    candidate ID whose excerpt holds it -- the only two facts `confirm` is
    allowed to print on rejection."""

    shared_run: str
    source_candidate_id: str


def check_description_provenance(
    description: str,
    public_git_text: str,
    excerpts_by_candidate_id: Mapping[str, str],
) -> ProvenanceViolation | None:
    """None when `description` is clear to promote; otherwise a word run it
    shares with some candidate's `.local/` finding excerpt, unless that run
    also occurs in `public_git_text` -- `git show` of the defect's
    introducing and fix commits (see `public_git_text` below) -- which
    marks it as shared code or an identifier rather than lifted prose.

    The run length is `_SIX_GRAM_WINDOW` (six words) whenever both texts
    are long enough for that window. When either the description or a
    given excerpt tokenizes to fewer than six words, the window shrinks to
    the shorter of the two lengths instead of skipping the comparison, so a
    short description can still be caught reusing a short excerpt's exact
    wording.

    This check only detects a verbatim or near-verbatim word run. A
    paraphrase, a reordering, or a synonym swap defeats it, so the human
    drafting the description remains the primary safeguard against a leak.

    Checked against every candidate's excerpt in `excerpts_by_candidate_id`,
    not only the one this description confirms, since the drafting session
    saw the whole shortlist.
    """
    description_tokens = _tokenize(description)
    if not description_tokens:
        return None
    public_tokens = _tokenize(public_git_text)

    for candidate_id in sorted(excerpts_by_candidate_id):
        excerpt_tokens = _tokenize(excerpts_by_candidate_id[candidate_id])
        if not excerpt_tokens:
            continue
        window = min(_SIX_GRAM_WINDOW, len(description_tokens), len(excerpt_tokens))
        excerpt_grams = set(_n_grams(excerpt_tokens, window))
        public_grams = set(_n_grams(public_tokens, window))
        for gram in _n_grams(description_tokens, window):
            if gram in public_grams:
                continue
            if gram in excerpt_grams:
                return ProvenanceViolation(gram, candidate_id)
    return None


# Local git call only, no network I/O -- mirrors mine_szz.py's own
# _LOCAL_GIT_TIMEOUT_S rationale (guards against a hung local git blocking
# `confirm` with no exit). Duplicated rather than imported because
# mine_szz.py already imports this module, and importing back would create
# a circular import. No vendor documentation grounds the 10-second
# magnitude; it's an empirical guess matching mine_szz.py's own constant.
_LOCAL_GIT_TIMEOUT_S = 10.0


def public_git_text(repo_dir: Path, introducing_commit: str, fix_commit: str, *, run=subprocess.run) -> str:
    """The defect's public git text: `git show` of its introducing commit
    plus its fix commit -- what `check_description_provenance` treats as
    legitimately shared code or identifiers, never a leaked `.local/`
    excerpt."""
    parts = []
    for commit in (introducing_commit, fix_commit):
        result = run(
            ["git", "show", commit], cwd=repo_dir, capture_output=True, text=True,
            timeout=_LOCAL_GIT_TIMEOUT_S, check=True,
        )
        parts.append(result.stdout)
    return "\n".join(parts)
