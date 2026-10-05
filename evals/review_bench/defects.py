"""Defect-set schema for the review bench: `Candidate` and `ConfirmedDefect` records,
their validation, JSON load/save, and the description-provenance check
`confirm` (evals/run_review_bench.py) runs before promoting a candidate into
the committed evals/review_bench/defects.json. `Candidate` records themselves
come from mine_szz.py and mine_review_rounds.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from review_bench.arms import LENS_READ_CLAUSES
from review_bench.local_git import DIFF_TEXT_ARGS, isolated_git_environment

# The lenses whose agent body carries a read clause -- the only lenses
# the review bench's function-context arm can substitute a clause for.
# staff-data-engineer and staff-product-engineer own other code-review
# checklist items but have no read clause to replace, so a defect either of
# them owns can never enter this benchmark. Derived from
# arms.LENS_READ_CLAUSES rather than a second hand-typed copy, so a lens
# added or removed there can't drift out of sync here.
KNOWN_LENSES: frozenset[str] = frozenset(LENS_READ_CLAUSES)

KNOWN_SOURCES: frozenset[str] = frozenset({"szz", "review-round", "pr-comment"})

# Sources whose candidate stores the public GitHub comment text it was mined
# from, which counts as public text for `check_description_provenance`.
GITHUB_COMMENT_SOURCES: frozenset[str] = frozenset({"pr-comment"})

_MARKDOWN_SUFFIXES: frozenset[str] = frozenset({".md", ".markdown"})

# mine_review_rounds.resolve_branch_ref's four possible outcomes for
# resolving a review round's branch to a reachable ref.
KNOWN_REF_STATUSES: frozenset[str] = frozenset({"local-branch", "fetched", "fetch-failed", "pr-unknown"})

_SHA_RE = re.compile(r"[0-9a-f]{40}")


def _validate_sha(field_name: str, value: str) -> None:
    # fullmatch, not `$`: a trailing newline must not pass as a SHA.
    if not _SHA_RE.fullmatch(value):
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
    `file_is_markdown` are the miner's advisory guesses for three of the four
    inclusion fields, and `lens` is its guess for the fourth (the owning
    lens). `cmd_confirm` shows all four at its `[y/N/q]` prompt, and the
    engineer's `y` is the approval. A non-empty `description` only marks a
    candidate as written up. `ConfirmedDefect` carries `lens` and none of
    the three guesses.
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


# The characters of the terminal-unsafe set a stored description may still hold,
# which is also what a mined comment body may hold.
STORED_DESCRIPTION_ALLOWED: frozenset[str] = frozenset({"\n", "\t"})

# ConfirmedDefect's exact field set -- no field outside the schema, so
# description is the only free text field.
_CONFIRMED_DEFECT_FIELDS: frozenset[str] = frozenset({
    "id", "source", "lens", "base_commit", "head_commit", "fix_commit", "fix_date", "description",
    "path", "file_is_markdown",
})


@dataclass(frozen=True)
class ConfirmedDefect:
    """One engineer-confirmed defect, committed to evals/review_bench/defects.json.

    `description` is the only free-text field. `confirm` runs it through
    `check_description_provenance`, which catches a verbatim or
    near-verbatim word run shared with a `.local/` finding excerpt --
    see that function's own docstring for the check's limits. Construction
    rejects a description holding a character `is_terminal_unsafe_character`
    flags, LF and TAB aside, so every loader inherits that rule.

    `path` is the candidate's `evidence["path"]` -- the head path for a
    `pr-comment` -- and `file_is_markdown` is the engineer-confirmed kind of
    that file. Both fields are required, so a record missing either fails to
    load.
    """

    id: str
    source: str
    lens: str
    base_commit: str
    head_commit: str
    fix_commit: str
    fix_date: str
    description: str
    path: str
    file_is_markdown: bool

    def __post_init__(self) -> None:
        _validate_common(
            lens=self.lens, source=self.source, base_commit=self.base_commit,
            head_commit=self.head_commit, fix_commit=self.fix_commit, fix_date=self.fix_date,
        )
        if not isinstance(self.path, str) or not self.path:
            raise ValueError(f"path must be a non-empty string, got {self.path!r}")
        if not isinstance(self.description, str):
            raise ValueError(f"description must be a string, got {self.description!r}")
        # LF and TAB stay legal, since a mined comment body can hold them.
        disallowed = first_disallowed_character(self.description, allowed=STORED_DESCRIPTION_ALLOWED)
        if disallowed is not None:
            raise ValueError(f"description holds the disallowed character U+{ord(disallowed):04X}")
        # isinstance, not truthiness: a JSON string like "false" is truthy.
        if not isinstance(self.file_is_markdown, bool):
            raise ValueError(f"file_is_markdown must be a bool, got {self.file_is_markdown!r}")

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


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` via a same-directory temp file plus
    `os.replace`, so a process crash mid-write or an overlapping writer leaves the
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
    atomic_write_text(path, json.dumps([c.to_dict() for c in candidates], indent=2, sort_keys=True) + "\n")


def load_confirmed_defects(path: Path) -> list[ConfirmedDefect]:
    if not path.exists():
        return []
    return [ConfirmedDefect.from_dict(d) for d in json.loads(path.read_text())]


def save_confirmed_defects(path: Path, defects: list[ConfirmedDefect]) -> None:
    atomic_write_text(path, json.dumps([d.to_dict() for d in defects], indent=2, sort_keys=True) + "\n")


def is_markdown_path(path: str) -> bool:
    """True when the last path component's suffix, lowercased, is `.md` or
    `.markdown`. The one markdown predicate every miner and `guess_lens` share,
    so `x.MD` and `notes.markdown` match and `docs.md/x.py` and `x.mdx` do not."""
    return PurePosixPath(path).suffix.lower() in _MARKDOWN_SUFFIXES


# Unicode categories whose characters can drive a terminal or render as nothing.
# Cn (unassigned) is absent: its membership follows the interpreter's Unicode
# version, so a committed description could load on one interpreter and fail on
# another. The fixed ranges below cover the unassigned code points worth rejecting.
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Zl", "Zp"})
# Characters outside those categories that render as nothing or hide the glyph
# before them: the Default_Ignorable_Code_Point characters of category Mn or Lo
# (combining grapheme joiner, Khmer inherent vowels, variation selectors, Hangul
# fillers), the Default_Ignorable code points reserved without an assignment, and
# the Braille blank.
_INVISIBLE_RANGES = (
    (0x034F, 0x034F),  # combining grapheme joiner
    (0x17B4, 0x17B5),  # Khmer inherent vowels
    (0x180B, 0x180D), (0x180F, 0x180F),  # Mongolian free variation selectors
    (0xFE00, 0xFE0F), (0xE0100, 0xE01EF),  # variation selectors, and their supplement
    (0x115F, 0x1160), (0x3164, 0x3164), (0xFFA0, 0xFFA0),  # Hangul fillers
    (0x2065, 0x2065), (0xFFF0, 0xFFF8),  # reserved Default_Ignorable code points
    (0xE0000, 0xE0000), (0xE0002, 0xE001F), (0xE0080, 0xE00FF), (0xE01F0, 0xE0FFF),
    (0x2800, 0x2800),  # Braille pattern blank
)
# The Unicode noncharacters: U+FDD0-FDEF, and the last two code points of every
# plane. The standard fixes the set, so it does not move with the interpreter.
_NONCHARACTER_RANGES = (
    (0xFDD0, 0xFDEF),
    *((plane * 0x10000 + 0xFFFE, plane * 0x10000 + 0xFFFF) for plane in range(17)),
)


def _in_ranges(code_point: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(low <= code_point <= high for low, high in ranges)


def is_terminal_unsafe_character(char: str) -> bool:
    """True for a character no description may hold and no terminal display
    may print raw: a control, format, separator, surrogate, private-use, or
    noncharacter code point, a variation selector, or a blank filler. The one
    definition behind `ConfirmedDefect.description`, a mined comment body, a
    typed description, and `escape_for_terminal`."""
    code_point = ord(char)
    return (
        unicodedata.category(char) in _UNSAFE_CATEGORIES
        or _in_ranges(code_point, _INVISIBLE_RANGES)
        or _in_ranges(code_point, _NONCHARACTER_RANGES)
    )


def is_invisible_character(char: str) -> bool:
    """True for a terminal-unsafe character that renders as nothing: a format
    character such as a joiner or bidi override, a variation selector, or a
    blank filler. The rest of the unsafe set is control, separator,
    private-use, or noncharacter code points."""
    return unicodedata.category(char) == "Cf" or _in_ranges(ord(char), _INVISIBLE_RANGES)


def first_disallowed_character(text: str, *, allowed: frozenset[str] = frozenset({"\n"})) -> str | None:
    """The first character of `text` that `is_terminal_unsafe_character` flags
    and `allowed` does not excuse, or None. A mined comment body and a typed
    description differ only in whether TAB is allowed."""
    return next((char for char in text if char not in allowed and is_terminal_unsafe_character(char)), None)


def has_disallowed_control_character(text: str, *, allowed: frozenset[str] = frozenset({"\n"})) -> bool:
    return first_disallowed_character(text, allowed=allowed) is not None


def escape_for_terminal(text: str) -> str:
    """`text` with each terminal-unsafe character shown as a backslash escape,
    so externally sourced text can never act on the operator's terminal."""
    return "".join(
        char.encode("unicode_escape").decode("ascii") if is_terminal_unsafe_character(char) else char for char in text
    )


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
    if is_markdown_path(path):
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
    public_texts: Sequence[str],
    excerpts_by_candidate_id: Mapping[str, str],
) -> ProvenanceViolation | None:
    """None when `description` is clear to promote; otherwise a word run it
    shares with some candidate's `.local/` finding excerpt, unless that run
    also occurs in one of `public_texts` -- see `defect_public_texts` below --
    which marks it as shared code, an identifier, or the engineer's own
    public comment rather than lifted prose. Each public text is checked on
    its own, so a run that spans the join of two texts is not exempt.

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

    Scoped to phrase reuse, not secret-shaped strings. The commit-time
    credential-value gate, `deny-pii-in-commits.sh`, remains the control of
    record for verbatim secret leakage, and it covers vendor-fixed value shapes
    only (GitHub token prefixes, AWS access key IDs, PEM private-key headers).
    `redact-credential-values.sh` only redacts tool results and cannot block a
    commit.
    """
    if isinstance(public_texts, str):  # a bare string would iterate as one text per character
        raise TypeError("public_texts must be a sequence of texts, not a single string")
    description_tokens = _tokenize(description)
    if not description_tokens:
        return None
    public_token_lists = [_tokenize(text) for text in public_texts]
    public_grams_by_window: dict[int, set] = {}

    for candidate_id in sorted(excerpts_by_candidate_id):
        excerpt_tokens = _tokenize(excerpts_by_candidate_id[candidate_id])
        if not excerpt_tokens:
            continue
        window = min(_SIX_GRAM_WINDOW, len(description_tokens), len(excerpt_tokens))
        excerpt_grams = set(_n_grams(excerpt_tokens, window))
        if window not in public_grams_by_window:
            public_grams_by_window[window] = {
                gram for tokens in public_token_lists for gram in _n_grams(tokens, window)
            }
        public_grams = public_grams_by_window[window]
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
# magnitude -- it's an empirical guess matching mine_szz.py's own constant.
_LOCAL_GIT_TIMEOUT_S = 10.0


def defect_pin_ref(defect_id: str) -> str:
    """The local ref that keeps one confirmed defect's fix commit, and so
    its introducing commit and base, from being pruned. A defect id can hold
    characters a ref name forbids (`:`, `~`), so the ref name is a digest."""
    digest = hashlib.sha256(defect_id.encode()).hexdigest()[:16]
    return f"refs/review-bench/defect/{digest}"


def pin_defect_commits(repo_dir: Path, defect: ConfirmedDefect, *, run=subprocess.run) -> str:
    """Point `defect_pin_ref(defect.id)` at the defect's fix commit and
    return the ref name. Local only, never pushed. Re-running with the same
    defect leaves the ref unchanged. `head_commit` and `base_commit` are
    ancestors of `fix_commit`, so this one ref keeps all three reachable."""
    ref = defect_pin_ref(defect.id)
    run(
        ["git", "update-ref", ref, defect.fix_commit], cwd=repo_dir, capture_output=True, encoding="utf-8", errors="replace",
        timeout=_LOCAL_GIT_TIMEOUT_S, check=True,
    )
    return ref


def commit_subjects(repo_dir: Path, commits: Sequence[str], *, run=subprocess.run) -> list[tuple[str, str]]:
    """`(sha, subject)` for each distinct commit in `commits`, in the order
    given, from one `git log --no-walk=unsorted` call. Raises ValueError for a
    commit that is not a 40-hex SHA, so none can read as a git option. The
    output is NUL-delimited bytes, because text mode would turn a `\r` inside
    a subject into a line break and split one entry in two."""
    for commit in commits:
        _validate_sha("commit", commit)
    result = run(
        ["git", "log", "-z", "--no-walk=unsorted", "--format=%H %s", *dict.fromkeys(commits)], cwd=repo_dir,
        capture_output=True, timeout=_LOCAL_GIT_TIMEOUT_S, check=True,
    )
    subjects = []
    for entry in result.stdout.decode("utf-8", errors="replace").split("\0"):
        if entry:
            sha, _separator, subject = entry.partition(" ")
            subjects.append((sha, subject))
    return subjects


def public_git_text(repo_dir: Path, introducing_commit: str, fix_commit: str, *, run=subprocess.run) -> str:
    """The defect's public git text: `git show` of its introducing commit
    plus its fix commit -- what `check_description_provenance` treats as
    legitimately shared code or identifiers, never a leaked `.local/`
    excerpt. Undecodable bytes are replaced, so one non-UTF-8 commit cannot
    abort a run."""
    parts = []
    for commit in (introducing_commit, fix_commit):
        result = run(
            ["git", "show", *DIFF_TEXT_ARGS, commit], cwd=repo_dir, capture_output=True, encoding="utf-8",
            errors="replace", timeout=_LOCAL_GIT_TIMEOUT_S, check=True, env=isolated_git_environment(),
        )
        parts.append(result.stdout)
    return "\n".join(parts)


def defect_public_texts(
    repo_dir: Path, introducing_commit: str, fix_commit: str, *, source: str, evidence: Mapping | None = None,
) -> list[str]:
    """The texts `check_description_provenance` treats as public for one
    defect: `public_git_text`, plus the stored comment text in
    `evidence["public_comment_text"]` when `source` is in
    `GITHUB_COMMENT_SOURCES`. A source outside that set gets no comment
    exemption even if its evidence carries the field."""
    texts = [public_git_text(repo_dir, introducing_commit, fix_commit)]
    comment_text = (evidence or {}).get("public_comment_text")
    if source in GITHUB_COMMENT_SOURCES and isinstance(comment_text, str) and comment_text:
        texts.append(comment_text)
    return texts
