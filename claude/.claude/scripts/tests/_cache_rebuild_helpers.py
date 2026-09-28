"""Test helpers shared by the cache-rebuild family's test files
(test_transcript_cache_rebuild*.py) and by test_transcript_analysis.py's
cross-subcommand tables."""
from __future__ import annotations

import re

from .conftest import _priced


def _cache_rebuild_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    threshold: int | None = None,
    no_redact: bool = False,
    extra_config_dirs: list[str] | None = None,
    ttl_verdict: bool = False,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "threshold": threshold,
        "no_redact": no_redact,
        "extra_config_dirs": extra_config_dirs,
        "ttl_verdict": ttl_verdict,
    })()


def _extract_cache_rebuild_summary(out: str) -> dict[str, str]:
    """Read cache-rebuild's 'Calls scanned: N' / 'Calls writing >= T tokens:
    N' summary lines as {"scanned": ..., "tail": ...}."""
    scanned = re.search(r"Calls scanned: ([\d,]+)", out)
    tail = re.search(r"Calls writing >= [\d,]+ tokens: ([\d,]+)", out)
    assert scanned is not None, "'Calls scanned' line not found in output"
    assert tail is not None, "'Calls writing >= ... tokens' line not found in output"
    return {"scanned": scanned.group(1).replace(",", ""), "tail": tail.group(1).replace(",", "")}


def _extract_cache_rebuild_row(out: str, row_label: str) -> tuple[int, str]:
    """Read one (count, dollars) row from cache-rebuild's cause-breakdown,
    concurrency-split, or per-account table by its leading label -- labels
    may contain spaces, so this matches the row as a literal line prefix
    rather than reusing _table_cols' one-token-per-column model. The
    cause-breakdown table has no dollars column, so a 1-cell row is read as
    (count, "")."""
    for line in out.splitlines():
        if line.startswith(row_label):
            rest = line[len(row_label):].split()
            if len(rest) == 1:
                return int(rest[0].replace(",", "")), ""
            if len(rest) == 2:
                return int(rest[0].replace(",", "")), rest[1]
    raise AssertionError(f"row not found for {row_label!r}")


def _extract_cache_rebuild_attribution_row(out: str, row_label: str) -> tuple[int, str, str, str]:
    """Read one (rebuilds, excess $, 5m-1h $, median cov.) row from the
    subagent idle-gap cause-attribution table by its leading label -- the
    5-cell sibling _extract_cache_rebuild_row (label + 1-2 numeric cells)
    cannot parse, the same "neither existing extractor fits" precedent
    _extract_cache_rebuild_dispersion sets below."""
    for line in out.splitlines():
        if line.startswith(row_label):
            rest = line[len(row_label):].split()
            if len(rest) != 4:
                raise AssertionError(f"expected 4 cells after label {row_label!r}, got {rest!r}")
            rebuilds, excess, band_excess, median_cov = rest
            return int(rebuilds.replace(",", "")), excess, band_excess, median_cov
    raise AssertionError(f"row not found for {row_label!r}")


def _extract_cache_rebuild_dispersion(out: str) -> dict[str, object]:
    """Read cache-rebuild's 'Subagent per-dispatch dispersion' block's
    labeled stat lines and its own coverage-disclosure line -- neither
    _extract_cache_rebuild_row (label + 1-2 numeric cells) nor _table_cols
    (fixed-width table rows) can parse these, since each is its own
    free-text sentence, not a table row. Matches on exact sentence wording,
    not header position like _table_cols -- a wording-only edit to the
    dispersion block's own print statements, with no behavior change,
    breaks every test using this extractor."""
    eligible = re.search(r"Subagent dispatches \(dispatches with any 5m-tier write\): ([\d,]+)", out)
    clearing = re.search(r"Dispatches individually clearing their own break-even ratio: ([\d,]+)", out)
    share = re.search(r"Their share of per-dispatch subagent W5m \(not the pooled row above\): ([\d.]+%)", out)
    net = re.search(r"Net \$ restricted to clearing dispatches: (-?[\d,]+\.\d\d)", out)
    uncovered = re.search(r"\(([\d,]+) of ([\d,]+) pooled subagent W5m tokens landed in no dispatch group", out)
    assert eligible is not None, "'Subagent dispatches' line not found in output"
    assert clearing is not None, "'Dispatches individually clearing' line not found in output"
    assert share is not None, "'Their share of per-dispatch subagent W5m' line not found in output"
    assert net is not None, "'Net $ restricted to clearing dispatches' line not found in output"
    assert uncovered is not None, "dispersion coverage-disclosure line not found in output"
    return {
        "eligible": int(eligible.group(1).replace(",", "")),
        "clearing": int(clearing.group(1).replace(",", "")),
        "share": share.group(1),
        "net": net.group(1).replace(",", ""),
        "uncovered_w5m": int(uncovered.group(1).replace(",", "")),
        "pooled_subagent_w5m": int(uncovered.group(2).replace(",", "")),
    }


# Regex-extracts from --ttl-verdict's markdown text since it has no
# structured (--json) output mode; migrate to parsing that instead if one
# is ever added.
def _extract_ttl_verdict_summary(out: str, origin: str) -> dict[str, str]:
    """Read --ttl-verdict's own per-bucket summary line ('main: consistent
    5m roots=N  consistent 1h roots=N  excluded (near-tie or no
    data) roots=N  verdict=...') for one bucket."""
    match = re.search(
        rf"^{re.escape(origin)}: consistent 5m roots=(\d+)  consistent 1h roots=(\d+)"
        r"  excluded \(near-tie or no data\) roots=(\d+)  verdict=(.+)$",
        out, re.MULTILINE,
    )
    assert match is not None, f"ttl-verdict summary line not found for origin {origin!r}"
    return {
        "consistent_5m": match.group(1),
        "consistent_1h": match.group(2),
        "excluded": match.group(3),
        "verdict": match.group(4),
    }


def _extract_ttl_verdict_root_row(out: str, origin: str, root_label: str) -> dict[str, str]:
    """Read one per-root row from --ttl-verdict's own per-bucket table (the
    '### {origin}' section) by its leading account label. _table_cols can't
    be reused directly since this section's header repeats once per origin.
    Matching on the exact leading token, not a startswith prefix, avoids
    "account-1" matching "account-10"."""
    lines = out.splitlines()
    section_start = lines.index(f"### {origin}")
    section_end = len(lines)
    for i in range(section_start + 1, len(lines)):
        if lines[i].startswith("### ") or lines[i].startswith(f"{origin}: consistent"):
            section_end = i
            break
    section_lines = lines[section_start:section_end]
    header_line = next(ln for ln in section_lines if ln.startswith("Root"))
    labels = header_line.split()
    rows = [ln for ln in section_lines if ln.split() and ln.split()[0] == root_label]
    assert len(rows) == 1, f"row not found for {root_label!r} in {origin!r} section: {rows!r}"
    return dict(zip(labels, rows[0].split(), strict=False))


def _extract_ttl_verdict_tier_split_line(out: str, root_label: str) -> dict[str, str] | None:
    """Parse one root's 'tier-split {label}: 5m-slice favors X, 1h-slice
    favors Y (agree|disagree)' line, if present. Returns None when the
    root has no tier-split line (not a mixed root)."""
    match = re.search(
        rf"^tier-split {re.escape(root_label)}: 5m-slice favors (\w+), "
        rf"1h-slice favors (\w+) \((\w+)\)$",
        out, re.MULTILINE,
    )
    if match is None:
        return None
    return {
        "favors_5m_slice": match.group(1),
        "favors_1h_slice": match.group(2),
        "agreement": match.group(3),
    }


def _ttl_verdict_5m_tier_adopt_records() -> list[dict]:
    """Record list for a clean 5m-tier root that reaches --ttl-verdict's
    'adopt' verdict (W5m=1,000,000, X=500,000, comfortably clearing
    margin), shared by test_main_bucket_5m_tier_root_reaches_adopt and by
    root_a in
    test_two_roots_of_different_tiers_in_same_bucket_combine_into_bucket_verdict."""
    return [
        _priced("claude-sonnet-5", ephemeral_5m=500_000, ts="2026-08-01T10:00:00.000Z", request_id="m1"),
        _priced(
            "claude-sonnet-5", ephemeral_5m=500_000,
            ts="2026-08-01T10:06:00.000Z", request_id="m2",
        ),
        _priced(
            "claude-sonnet-5", cache_read=300_000,
            ts="2026-08-01T10:12:00.000Z", request_id="m3",
        ),
    ]


def _ttl_verdict_1h_tier_non_wash_disagreement_records() -> list[dict]:
    """Record list for a 1h-tier root whose dollar accounting and
    tiebreaker disagree (W1h=1,000,000, primary Z=800,000, favors 1h,
    does not clear), shared by
    test_1h_tier_root_declines_on_a_non_wash_disagreement and by root_b in
    test_two_roots_of_different_tiers_in_same_bucket_combine_into_bucket_verdict."""
    return [
        _priced("claude-sonnet-5", ephemeral_1h=1_000_000, ts="2026-08-01T10:00:00.000Z", request_id="w1h-1"),
        _priced(
            "claude-sonnet-5", cache_read=800_000,
            ts="2026-08-01T10:06:40.000Z", request_id="w1h-2",
        ),
        _priced(
            "claude-sonnet-5", cache_read=400_000,
            ts="2026-08-01T10:08:20.000Z", request_id="w1h-3",
        ),
    ]


def _ttl_verdict_near_tie_mixed_root_records(request_id_prefix: str = "a") -> list[dict]:
    """Record list for a mixed root at share 0.870, below
    _CACHE_REBUILD_TTL_DOMINANT_TIER_SHARE_MIN (5m 500,000 @10:00, 5m
    500,000 @10:06, 1h 150,000 @10:13). request_id_prefix keeps request
    IDs unique when this shape is reused for a second bucket or origin
    in the same test's output."""
    return [
        _priced(
            "claude-sonnet-5", ephemeral_5m=500_000,
            ts="2026-08-01T10:00:00.000Z", request_id=f"{request_id_prefix}1",
        ),
        _priced(
            "claude-sonnet-5", ephemeral_5m=500_000,
            ts="2026-08-01T10:06:00.000Z", request_id=f"{request_id_prefix}2",
        ),
        _priced(
            "claude-sonnet-5", ephemeral_1h=150_000,
            ts="2026-08-01T10:13:00.000Z", request_id=f"{request_id_prefix}3",
        ),
    ]


def _ttl_verdict_dominant_1h_mixed_root_records() -> list[dict]:
    """Record list for a mixed root at share 0.800, below the dominance
    threshold and excluded/near-tie (5m 200,000 @10:00, 1h 800,000 @10:06,
    read 150,000 @10:12)."""
    return [
        _priced("claude-sonnet-5", ephemeral_5m=200_000, ts="2026-08-01T10:00:00.000Z", request_id="dom-1"),
        _priced("claude-sonnet-5", ephemeral_1h=800_000, ts="2026-08-01T10:06:00.000Z", request_id="dom-2"),
        _priced("claude-sonnet-5", cache_read=150_000, ts="2026-08-01T10:12:00.000Z", request_id="dom-3"),
    ]
