"""Tests for transcript_analysis/cost.py (cmd_cost, cmd_cost_trend)."""
import importlib.util
import os
import re
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from conftest import (
    _agent_use,
    _asst,
    _audit_routing_args,
    _context_distribution_args,
    _cost_args,
    _cost_trend_args,
    _extract_grand_total,
    _opus,
    _priced,
    _table_cols,
    _user_msg,
    _write_cost_root,
    _write_jsonl,
    _write_subagent_jsonl,
)

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


# ---------------------------------------------------------------------------
# cost
# ---------------------------------------------------------------------------


def _md_table_cols(out: str, *, header_contains: str, row_contains: str | Sequence[str],
                    occurrence: int | None = None) -> dict[str, str]:
    """Map column-label -> cell value for the GFM pipe-table data row matching
    `row_contains` -- the markdown-table counterpart to _table_cols, splitting
    on `|` instead of whitespace since a markdown cell (a model ID, a share
    percentage) may itself contain no whitespace token boundary to split on.

    Mirrors _table_cols' anchoring (locates the section by the header line,
    scoped through the next blank line or repeated header) and its
    fail-loud-on-ambiguous-match semantics: exactly one header / one matching
    data row must be found unless `occurrence` disambiguates a repeated
    header. The `|---|---|...|` separator row never satisfies `row_contains`
    (it has no cell text to match), so it needs no special-casing to stay
    out of the returned row.
    """
    lines = out.splitlines()
    header_indices = [i for i, ln in enumerate(lines) if header_contains in ln]
    if occurrence is None:
        assert len(header_indices) == 1, f"header match not unique for {header_contains!r}: {len(header_indices)}"
        start = header_indices[0]
    else:
        assert len(header_indices) >= occurrence, (
            f"header occurrence {occurrence} requested but only {len(header_indices)} "
            f"found for {header_contains!r}"
        )
        start = header_indices[occurrence - 1]
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if not lines[i].strip() or header_contains in lines[i]:
            end = i
            break
    section_lines = lines[start:end]

    headers = [ln for ln in section_lines if header_contains in ln]
    assert len(headers) == 1, f"header match not unique for {header_contains!r}: {len(headers)}"
    header = headers[0]

    needles = (row_contains,) if isinstance(row_contains, str) else tuple(row_contains)
    rows = [ln for ln in section_lines if ln != header and all(n in ln for n in needles)]
    assert len(rows) == 1, f"row match not unique for {row_contains!r}: {len(rows)}"
    labels = [c.strip() for c in header.strip().strip("|").split("|")]
    values = [c.strip() for c in rows[0].strip().strip("|").split("|")]
    assert len(values) >= len(labels), f"row has fewer cells than labels: {rows[0]!r}"
    return dict(zip(labels, values, strict=False))


def _extract_unpriced_total(out: str) -> int:
    """Read cmd_cost's 'Unpriced tokens (unknown model IDs): N' line as an int.

    A single named extractor for this one non-tabular summary line, matching
    _table_cols' role for tabular output — one parse point to update if the
    line's wording changes, instead of an inline regex at each call site.
    """
    match = re.search(r"Unpriced tokens \(unknown model IDs\): ([\d,]+)", out)
    assert match is not None, "unpriced-tokens summary line not found in output"
    return int(match.group(1).replace(",", ""))


def _extract_md_grand_total(out: str) -> float:
    """Read --summary's bolded grand-total row ('| **total** | **X.XX** | | |')
    from the markdown token-class table."""
    match = re.search(r"^\|\s*\*\*total\*\*\s*\|\s*\*\*([\d,]+\.\d\d)\*\*\s*\|", out, re.MULTILINE)
    assert match is not None, "markdown grand total row not found in output"
    return float(match.group(1).replace(",", ""))


def _extract_account_totals(out: str) -> dict[int, float]:
    """Map account ordinal -> that account's own token-class 'total' row
    dollar figure, by splitting cost's '## Cost by account' section on its
    own '### account-N' sub-headers."""
    totals: dict[int, float] = {}
    for block in out.split("### account-")[1:]:
        ordinal_str, _, rest = block.partition("\n")
        match = re.search(r"^total\s+([\d,]+\.\d\d)\s*$", rest, re.MULTILINE)
        assert match is not None, f"no total row found for account-{ordinal_str.strip()}"
        totals[int(ordinal_str.strip())] = float(match.group(1).replace(",", ""))
    return totals


def _extract_summary_unpriced(out: str) -> tuple[int, int]:
    """Read --summary's dedicated 'Unpriced tokens: N tokens across M model IDs'
    line as (N, M) — distinct from the full report's own
    'Unpriced tokens (unknown model IDs): N' line, which _extract_unpriced_total
    reads."""
    match = re.search(r"Unpriced tokens: ([\d,]+) tokens across (\d+) model IDs", out)
    assert match is not None, "summary unpriced-tokens line not found in output"
    return int(match.group(1).replace(",", "")), int(match.group(2))


def _two_declared_roots_with_this_repo_sessions(tmp_path, monkeypatch) -> tuple[Path, Path]:
    """Active profile (config_dir() via CLAUDE_CONFIG_DIR) plus one declared
    root (via TRANSCRIPT_CONFIG_DIRS_FILE), each holding a matching --this-repo
    project dir ("-repo-main") with a nonzero-cost transcript on branch
    "main" -- the minimal fixture mechanism 1's --summary narrowing test and
    its non-summary union counterpart share. Mocks `git worktree list` /
    `git rev-parse --show-toplevel` the same way every other --this-repo
    TestCostSummary fixture does."""
    active = tmp_path / "acct-active"
    other = tmp_path / "acct-other"
    (active / "projects").mkdir(parents=True)
    (other / "projects").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(active))
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{other}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

    slug = "-repo-main"
    proj_active = active / "projects" / slug
    proj_active.mkdir(parents=True)
    _write_jsonl(proj_active / "sess-active.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00
    proj_other = other / "projects" / slug
    proj_other.mkdir(parents=True)
    _write_jsonl(proj_other / "sess-other.jsonl", [_priced("claude-sonnet-5", input=5_000_000)])  # $10.00

    monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

    def fake_run(cmd, *a, **k):
        if cmd[:3] == ["git", "worktree", "list"]:
            porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/main\n"
            return subprocess.CompletedProcess(cmd, 0, porcelain, "")
        assert cmd == ["git", "rev-parse", "--show-toplevel"]
        return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
    monkeypatch.setattr(subprocess, "run", fake_run)

    return active / "projects", other / "projects"


# ---------------------------------------------------------------------------
# cost-trend
# ---------------------------------------------------------------------------


def _extract_cost_trend_row(out: str, week_label: str) -> dict[str, str] | None:
    """Parse one cost-trend row. week_label may include the trailing ' (partial)'
    suffix — the label itself can be multi-word, so this matches as a literal
    line prefix rather than reusing _table_cols' one-token-per-column model."""
    for line in out.splitlines():
        if line.startswith(week_label):
            rest = line[len(week_label):].split()
            if len(rest) == 3:
                return {"total": rest[0], "context_pct": rest[1], "opus_pct": rest[2]}
    return None


class TestCost:
    def test_price_turn_hand_computed_dollar_total(self):
        """_price_turn's per-class dollars match a hand-computed total, read back
        through the named extractor (_price_turn itself) rather than a string
        match on formatted `$`-prefixed, comma-separated table output."""
        usage = _priced(
            "claude-sonnet-5",
            input=100_000, cache_read=200_000, ephemeral_1h=10_000, ephemeral_5m=20_000, output=5_000,
        )["message"]["usage"]
        dollars, context_at_turn, unpriced = _mod._price_turn("claude-sonnet-5", usage)
        assert unpriced == 0
        assert context_at_turn == 100_000 + 200_000 + 10_000 + 20_000
        # Sonnet 5 base $2/MTok: output 5x=$10, cache_write_5m 1.25x=$2.5,
        # cache_write_1h 2x=$4, cache_read 0.1x=$0.2, input=$2 (all per MTok).
        assert dollars["input"] == pytest.approx(100_000 / 1_000_000 * 2.0)
        assert dollars["output"] == pytest.approx(5_000 / 1_000_000 * 10.0)
        assert dollars["cache_read"] == pytest.approx(200_000 / 1_000_000 * 0.2)
        assert dollars["cache_write_1h"] == pytest.approx(10_000 / 1_000_000 * 4.0)
        assert dollars["cache_write_5m"] == pytest.approx(20_000 / 1_000_000 * 2.5)

    def test_raw_dollars_summed_before_rounding_not_after(self, fake_projects, capsys):
        """Three sub-cent turns ($0.166 each) sum to $0.498, rendering '0.50' — a
        round-then-sum bug would render each turn as $0.17 and total '0.51'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=83_000),
            _priced("claude-sonnet-5", input=83_000),
            _priced("claude-sonnet-5", input=83_000),
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert cols["$"] == "0.50"

    def test_nested_cache_creation_priced_separately_1h_and_5m(self):
        """Nested ephemeral_1h/ephemeral_5m tokens are split and never summed together."""
        usage = _priced("claude-sonnet-5", ephemeral_1h=10_000, ephemeral_5m=20_000)["message"]["usage"]
        assert _mod._cache_write_split(usage) == (10_000, 20_000)

    def test_flat_and_nested_both_present_not_double_counted(self):
        """flat cache_creation_input_tokens sums the nested fields on every real
        record (row2); the split must read only the nested block, never add
        flat on top of it."""
        usage = _priced("claude-sonnet-5", ephemeral_1h=10_000, ephemeral_5m=20_000)["message"]["usage"]
        assert usage["cache_creation_input_tokens"] == 30_000  # flat present too
        eph_1h, eph_5m = _mod._cache_write_split(usage)
        assert eph_1h + eph_5m == 30_000  # not 60,000 (double-counted)

    def test_flat_cache_creation_fallback_priced_as_5m_only(self):
        """Nested block absent (the untested-by-real-data fallback path): the flat
        total is priced entirely as 5m, at 1.25x — never split into 1h at 2x."""
        usage = _priced("claude-sonnet-5", flat_cache_creation=40_000)["message"]["usage"]
        assert "cache_creation" not in usage
        assert _mod._cache_write_split(usage) == (0, 40_000)
        dollars, _context_at_turn, unpriced = _mod._price_turn("claude-sonnet-5", usage)
        assert unpriced == 0
        assert dollars["cache_write_5m"] == pytest.approx(40_000 / 1_000_000 * 2.5)
        assert dollars["cache_write_1h"] == 0.0

    def test_empty_nested_cache_creation_block_not_treated_as_absent(self):
        """A present-but-empty nested block ({}) means zero of both ephemeral kinds —
        it must not fall through to the flat field as if the nested block were
        absent, even though both {} and a missing key are falsy."""
        usage = {
            "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 40_000, "cache_creation": {},
        }
        assert _mod._cache_write_split(usage) == (0, 0)

    def test_per_model_id_selection_sonnet5_vs_sonnet46(self, fake_projects, capsys):
        """Sonnet 5 ($2 base) and Sonnet 4.6 ($3 base) price the same input tokens differently."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000),
            _priced("claude-sonnet-4-6", input=1_000_000),
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        sonnet5 = _table_cols(out, header_contains="Model", row_contains="claude-sonnet-5")
        sonnet46 = _table_cols(out, header_contains="Model", row_contains="claude-sonnet-4-6")
        assert sonnet5["$"] == "2.00"
        assert sonnet46["$"] == "3.00"

    def test_claude_sonnet_4_5_prices_at_vendor_rate(self):
        """claude-sonnet-4-5's five derived rates match the vendor table ($3
        input / $15 output / $3.75 5m-write / $6 1h-write / $0.30 cache-read),
        via the same _model_rates derivation every other model in the table
        uses."""
        rates = _mod._model_rates("claude-sonnet-4-5")
        assert rates is not None
        assert rates["input"] == pytest.approx(3.00)
        assert rates["output"] == pytest.approx(15.00)
        assert rates["cache_write_5m"] == pytest.approx(3.75)
        assert rates["cache_write_1h"] == pytest.approx(6.00)
        assert rates["cache_read"] == pytest.approx(0.30)

    def test_claude_sonnet_4_5_dated_snapshot_unpriced_but_200k_bucketed(self):
        """_MODEL_BASE_INPUT_RATES is an exact-match dict: a dated-snapshot
        variant of claude-sonnet-4-5 still 200k-context-buckets correctly
        (prefix match) but prices as unpriced (exact-match miss) -- pins the
        asymmetry documented on the pricing-table entry."""
        dated_model = "claude-sonnet-4-5-20260115"
        assert _mod._model_rates(dated_model) is None
        assert _mod._context_window_for_model(dated_model) == 200_000

    def test_unknown_model_id_surfaced_and_excluded_from_total(self, fake_projects, capsys):
        """Unknown model IDs are named in the model table and their tokens counted
        separately, never silently folded into the priced total at $0."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("<synthetic>", input=1_000_000, output=500_000, cache_read=200_000),
            _priced("claude-sonnet-5", input=1_000_000),
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "<synthetic>" in out
        assert "unpriced" in out
        assert _extract_unpriced_total(out) == 1_000_000 + 500_000 + 200_000
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert cols["$"] == "2.00"  # only the priced sonnet-5 turn's $2/MTok * 1M input tokens

    def test_mixed_model_ids_within_session_sums_per_turn(self, fake_projects, capsys):
        """A session mixing Sonnet 5 and Sonnet 4.6 turns prices each against its own
        model ID; the session total is their sum, not a dominant-family pick
        (token-analyzer.py:16-23's per-session _fam shape, which this must not copy)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000),      # $2.00
            _priced("claude-sonnet-4-6", input=1_000_000),    # $3.00
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Session", row_contains="private-project-1")
        assert cols["$"] == "5.00"

    def test_context_bucket_threshold_boundaries(self):
        """199,999 is <200k; exactly 200,000 and 200,001 are >=200k (inclusive edge)."""
        assert _mod._context_bucket(199_999) == "<200k"
        assert _mod._context_bucket(200_000) == ">=200k"
        assert _mod._context_bucket(200_001) == ">=200k"

    def test_price_turn_rate_independent_of_context_bucket(self):
        """The $/token rate does not change with context size — pins row4 (no
        >200k long-context premium applies to any model in the corpus) against
        a future regression."""
        usage_small = _priced("claude-sonnet-5", input=199_999)["message"]["usage"]
        usage_large = _priced("claude-sonnet-5", input=200_001)["message"]["usage"]
        dollars_small, ctx_small, _ = _mod._price_turn("claude-sonnet-5", usage_small)
        dollars_large, ctx_large, _ = _mod._price_turn("claude-sonnet-5", usage_large)
        assert _mod._context_bucket(ctx_small) == "<200k"
        assert _mod._context_bucket(ctx_large) == ">=200k"
        assert dollars_small["input"] / 199_999 == pytest.approx(dollars_large["input"] / 200_001)

    def test_cost_by_context_bucket_section_reflects_boundary(self, fake_projects, capsys):
        """A <200k turn and a >=200k turn land in their respective bucket rows."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=100_000),    # <200k, $0.20
            _priced("claude-sonnet-5", input=1_000_000),  # >=200k, $2.00
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        under = _table_cols(out, header_contains="Bucket", row_contains="<200k")
        over = _table_cols(out, header_contains="Bucket", row_contains=">=200k")
        assert under["$"] == "0.20"
        assert over["$"] == "2.00"

    def test_empty_corpus_renders_clean_zero_state(self, fake_projects, capsys):
        """No priced turns at all: every share renders 0.0% and there's no traceback."""
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "0.0%" in out
        assert "(no priced turns in range)" in out

    def test_staleness_banner_fires_when_today_past_expires(self, fake_projects, capsys):
        """today past the default fetch-date+90d re-verify-by date: the banner
        fires in the same output block as the dollar tables, not a separate
        log line."""
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000)])
        _mod._cost_report(_cost_args(), date(2026, 11, 1))
        out = capsys.readouterr().out
        assert "STALE PRICING" in out
        assert "claude-sonnet-5" in out.split("## Cost by token class")[0]

    def test_staleness_banner_absent_when_today_before_expires(self, fake_projects, capsys):
        """today before expiry: no banner — a one-direction test would pass even
        if the banner always printed."""
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "STALE PRICING" not in out

    def test_sonnet_5_no_longer_flagged_stale_by_cancelled_promo_expiry_date(self, fake_projects, capsys):
        """The vendor cancelled Sonnet 5's Sept 1, 2026 rate increase, so
        today=2026-09-01 (past the old, now-removed 2026-08-31 promo-expiry
        date) must not fire the banner -- Sonnet 5 uses the same
        _DEFAULT_REVERIFY_BY schedule as every other model now."""
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000)])
        _mod._cost_report(_cost_args(), date(2026, 9, 1))
        out = capsys.readouterr().out
        assert "STALE PRICING" not in out

    def test_sidechain_turns_priced_exactly_once(self, fake_projects, capsys):
        """cost prices subagent (isSidechain) turns via include_subagents=True —
        exactly once, not skipped and not duplicated."""
        session_id = "sess-side"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000),  # main thread: $2.00
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _priced("claude-sonnet-5", input=1_000_000),  # sidechain: $2.00
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert cols["$"] == "4.00"

    def test_multi_record_request_id_group_priced_exactly_once(self, tmp_path, monkeypatch, capsys):
        """Three assistant records sharing one requestId (one JSONL record per
        content block, as Claude Code writes for a single API call) carry a
        byte-identical usage dict — cost prices the group's usage once, not
        once per record, and counts it as one priced turn, not three.

        Uses --summary (rather than this class's usual fake_projects fixture)
        since priced_turn_count is only rendered in --summary's output, and
        --summary requires --this-repo -- hence the git-worktree-list/getcwd
        mocks, mirroring TestCostSummary's own fixture pattern.
        """
        projects = tmp_path / "projects"
        (projects / "-repo-main").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        usage = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_input_tokens": 0}
        recs = [
            _asst("claude-sonnet-5", content=[{"type": "thinking", "thinking": "..."}], request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
                  request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "text", "text": "done"}], request_id="req-1"),
        ]
        for rec in recs:
            rec["message"]["usage"] = dict(usage)
        _write_jsonl(projects / "-repo-main" / "sess.jsonl", recs)
        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        # $2.00 (claude-sonnet-5's $2/MTok input rate on 1M input tokens) once,
        # not $6.00 for pricing the group's usage three times over.
        assert _extract_md_grand_total(out) == pytest.approx(2.00)
        # Three content-block records collapse into one priced turn, not three.
        assert "1 priced turns" in out

    def test_sidechain_multi_record_request_id_group_composes_with_sidechain_dedup(
        self, fake_projects, capsys
    ):
        """A sidechain (subagent-file) request split into two content-block
        records shares one requestId and is priced once for its own group —
        this composes with, but is a different mechanism from,
        test_sidechain_turns_priced_exactly_once's invariant above (dedup of
        subagent *files* vs. the main file, not requestId dedup)."""
        session_id = "sess-side-multi"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000),  # main thread: $2.00
        ])
        side_usage = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_input_tokens": 0}
        side_a = _asst("claude-sonnet-5", content=[{"type": "thinking", "thinking": "..."}],
                        request_id="req-side", sidechain=True)
        side_a["message"]["usage"] = dict(side_usage)
        side_b = _asst("claude-sonnet-5", content=[{"type": "text", "text": "done"}],
                        request_id="req-side", sidechain=True)
        side_b["message"]["usage"] = dict(side_usage)
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [side_a, side_b])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        # main $2.00 + sidechain $2.00 (priced once despite 2 content-block records) = $4.00.
        assert cols["$"] == "4.00"

    def test_redact_proj_label_map_miss_returns_opaque_token_not_raw_name(self):
        """A project label absent from the map renders as the fixed opaque token,
        never the raw project name — a map miss must fail closed. Built with
        two real labels so the map can express "one mapped, one missing"; a
        single-label fixture can't distinguish a miss from an empty map."""
        redact_map = {"project-a": "private-project-1"}  # "project-b" deliberately absent
        assert _mod._redact_proj_label("project-a", redact_map) == "private-project-1"
        assert _mod._redact_proj_label("project-b", redact_map) == _mod._REDACT_MAP_MISS_TOKEN
        assert _mod._REDACT_MAP_MISS_TOKEN != "project-b"

    def test_redact_session_id_map_miss_returns_opaque_token_not_raw_id(self):
        """Same fail-closed contract as the project-label map, for session IDs:
        a session_id absent from the run-scoped map renders as the fixed
        opaque token, never the raw session ID."""
        session_map = {"sess-real-id": "session-1"}  # "sess-other-id" deliberately absent
        assert _mod._redact_session_id("sess-real-id", session_map) == "session-1"
        assert _mod._redact_session_id("sess-other-id", session_map) == _mod.redaction._REDACT_SESSION_MISS_TOKEN
        assert _mod.redaction._REDACT_SESSION_MISS_TOKEN != "sess-other-id"

    def test_redact_proj_label_claude_config_passthrough_preserved(self):
        """claude-config still passes through unredacted after the fail-closed rewrite."""
        assert _mod._redact_proj_label("claude-config", {}) == "claude-config"

    def test_cost_redact_default_hides_project_names_and_session_ids(self, fake_projects, capsys):
        """Default (no --no-redact): raw project labels and raw session IDs are
        absent from stdout across every cost section — catches a partial leak,
        not just the total no-op test_redact_flag_anonymizes_project_names checks."""
        _write_jsonl(fake_projects / "sess-one.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        proj2 = fake_projects.parent / "-home-user-otherrepo"
        proj2.mkdir(parents=True)
        _write_jsonl(proj2 / "sess-two.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "testrepo" not in out
        assert "otherrepo" not in out
        assert "sess-one" not in out
        assert "sess-two" not in out
        assert "private-project-1" in out
        assert "private-project-2" in out

    def test_cost_no_redact_emits_real_label_and_session_id(self, fake_projects, capsys):
        """--no-redact emits the real project label and real session ID — proving
        the default redaction didn't silently become the only mode."""
        _write_jsonl(fake_projects / "sess-plain.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(no_redact=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "testrepo" in out
        assert "sess-plain" in out

    def test_shared_redact_map_binds_same_project_across_cost_and_audit_routing(self, fake_projects, capsys):
        """cost and audit-routing share _build_redact_map: a project binds to the
        same private-project-N placeholder from both, even though cost's own
        --projects filter here is narrower than the full corpus — a per-command
        map built only from the narrowed glob would assign zzzlast
        private-project-1 instead of -2, since it would be the only project seen."""
        proj_b = fake_projects.parent / "-home-user-zzzlast"
        proj_b.mkdir(parents=True)
        _write_jsonl(fake_projects / "sess-a.jsonl", [_opus([_agent_use("a1", "code-writer")], out=100)])
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _opus([_agent_use("a1", "code-writer")], out=100),
            _priced("claude-sonnet-5", input=1_000_000),
        ])

        expected_map = _mod._build_redact_map()
        assert expected_map["testrepo"] == "private-project-1"
        assert expected_map["zzzlast"] == "private-project-2"

        _mod.cmd_audit_routing(_audit_routing_args(projects="*", redact=True))
        audit_out = capsys.readouterr().out
        assert "private-project-2" in audit_out

        _mod._cost_report(_cost_args(projects="-home-user-zzzlast", no_redact=False), date(2026, 8, 2))
        cost_out = capsys.readouterr().out
        assert "private-project-2" in cost_out
        assert "private-project-1" not in cost_out  # testrepo's session wasn't in this narrowed run

    def test_since_filter_excludes_out_of_window_turn(self, fake_projects, capsys):
        """--since window: a turn timestamped outside the window is excluded from the total."""
        old_ts = "2020-01-01T00:00:00.000Z"   # far in the past — always out-of-window
        new_ts = "2099-12-31T00:00:00.000Z"   # far in the future — always in-window
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts=old_ts),
            _priced("claude-sonnet-5", input=2_000_000, ts=new_ts),
        ])
        _mod._cost_report(_cost_args(since="1d"), date(2026, 8, 2))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert cols["$"] == "4.00"  # only the 2,000,000-input-token turn is in-window

    def test_since_malformed_value_exits_nonzero_with_subcommand_in_message(self, fake_projects, capsys):
        """A malformed --since value fails closed with the cost-specific error prefix."""
        with pytest.raises(SystemExit):
            _mod._cost_report(_cost_args(since="not-a-window"), date(2026, 8, 2))
        assert "cost: --since: expected Nd like '35d'" in capsys.readouterr().err

    def test_top_n_truncates_session_rows(self, fake_projects, capsys):
        """--top N keeps only the N highest-dollar sessions; excluded sessions don't appear."""
        for i in range(5):
            _write_jsonl(fake_projects / f"sess-{i}.jsonl", [_priced("claude-sonnet-5", input=(i + 1) * 1_000_000)])
        _mod._cost_report(_cost_args(top=2), date(2026, 8, 2))
        out = capsys.readouterr().out
        top_section = out.split("## Top 2 sessions by dollars")[1]
        session_lines = [ln for ln in top_section.splitlines() if ln.startswith("session-")]
        assert len(session_lines) == 2
        # The two highest-dollar sessions (i=3,4 -> $8.00, $10.00) survive; i=0,1,2 are truncated.
        assert "10.00" in top_section
        assert "8.00" in top_section
        assert "2.00" not in top_section

    def test_this_repo_scopes_via_resolve_project_scope(self, tmp_path, monkeypatch, capsys):
        """cost wires --this-repo through the shared _resolve_project_scope helper
        like every other subcommand — pinned here because cost predates that
        convention and a prior rebase silently left it on a bespoke --projects-only
        argument with no --this-repo support at all."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(this_repo=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert (
            "COST SOURCES (this repo (1 project dirs); "
            "1 root (no ~/.claude/transcript-config-dirs declared))"
        ) in out
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert cols["$"] == "2.00"

    def test_redact_map_ordinal_shifts_with_out_of_scope_project(self, tmp_path, monkeypatch, capsys):
        """_build_redact_map's ordinals are assigned over the full local corpus,
        not the caller's --this-repo scope: pins that a --this-repo report's
        printed private-project-N number depends on other private projects that
        never appear in the report — the ordinal side-channel documented on
        _build_redact_map. "aardvark" sorts before "main" (this repo's derived
        label), so its mere presence on disk — outside --this-repo scope and
        never printed — bumps "main" from private-project-1 to private-project-2."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        # Run 1: only this repo's own project dir exists on disk.
        solo_projects = tmp_path / "solo" / "projects"
        mine_solo = solo_projects / "-repo-main"
        mine_solo.mkdir(parents=True)
        _write_jsonl(mine_solo / "s.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", solo_projects)
        _mod._cost_report(_cost_args(this_repo=True, no_redact=False), date(2026, 8, 2))
        solo_out = capsys.readouterr().out
        assert "private-project-1" in solo_out

        # Run 2: an out-of-scope project ("aardvark") sorts before this repo's
        # own label and is never surfaced by --this-repo, yet still shifts the
        # ordinal assigned to this repo's project.
        shared_projects = tmp_path / "shared" / "projects"
        mine_shared = shared_projects / "-repo-main"
        mine_shared.mkdir(parents=True)
        other = shared_projects / "-home-user-aardvark"
        other.mkdir(parents=True)
        _write_jsonl(mine_shared / "s.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _write_jsonl(other / "o.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", shared_projects)
        _mod._cost_report(_cost_args(this_repo=True, no_redact=False), date(2026, 8, 2))
        shared_out = capsys.readouterr().out
        assert "private-project-2" in shared_out
        assert "private-project-1" not in shared_out
        assert "aardvark" not in shared_out  # the out-of-scope project itself never prints

    def test_redact_map_ordinal_unaffected_by_out_of_scope_project_sorting_after(
        self, tmp_path, monkeypatch, capsys
    ):
        """Companion to the shifts-before case: an out-of-scope project whose
        derived label sorts *after* the in-scope one leaves the in-scope
        project's ordinal unchanged, since _build_redact_map assigns ordinals
        in alphabetical order. "zzz-other" sorts after "main", so it must not
        bump "main" off private-project-1."""
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        shared_projects = tmp_path / "shared" / "projects"
        mine_shared = shared_projects / "-repo-main"
        mine_shared.mkdir(parents=True)
        other = shared_projects / "-home-user-zzz-other"
        other.mkdir(parents=True)
        _write_jsonl(mine_shared / "s.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _write_jsonl(other / "o.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", shared_projects)
        _mod._cost_report(_cost_args(this_repo=True, no_redact=False), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "private-project-1" in out
        assert "zzz-other" not in out


# ---------------------------------------------------------------------------
# cost — multi-root (--config-dir)
# ---------------------------------------------------------------------------


class TestCostResolveRoots:
    """_resolve_cost_roots: the --config-dir CLI-boundary contract, mirroring
    post-crash-sessions.py:1067-1111's --config-dir (transcript-analysis.py's
    own sibling implementation of the same contract)."""

    def test_default_root_alone_when_no_extra_config_dirs(self, tmp_path, monkeypatch):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        roots = _mod._resolve_cost_roots(_cost_args())
        assert roots == [default_dir / "projects"]

    def test_single_extra_config_dir_allowed(self, tmp_path, monkeypatch, fake_config_dir_factory):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        roots = _mod._resolve_cost_roots(_cost_args(extra_config_dirs=[str(acct_b)]))
        assert roots == [default_dir / "projects", acct_b / "projects"]

    def test_extra_roots_appended_in_argument_order(self, tmp_path, monkeypatch, fake_config_dir_factory):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        acct_c = fake_config_dir_factory("acct-c")
        roots = _mod._resolve_cost_roots(_cost_args(extra_config_dirs=[str(acct_b), str(acct_c)]))
        assert roots == [default_dir / "projects", acct_b / "projects", acct_c / "projects"]

    def test_duplicate_root_deduped_by_resolve_not_string_equality(self, tmp_path, monkeypatch, fake_config_dir_factory):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        # Same directory, supplied twice under different (but equal-once-
        # resolved) spellings — the dedup guard is by .resolve(), not string
        # equality.
        roots = _mod._resolve_cost_roots(
            _cost_args(extra_config_dirs=[str(acct_b), str(acct_b) + "/."])
        )
        assert roots == [default_dir / "projects", acct_b / "projects"]

    def test_nonexistent_root_rejected_exit_2(self, tmp_path, monkeypatch, capsys):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        missing = tmp_path / "does-not-exist"
        with pytest.raises(SystemExit) as exc_info:
            _mod._resolve_cost_roots(_cost_args(extra_config_dirs=[str(missing)]))
        assert exc_info.value.code == 2
        assert str(missing) in capsys.readouterr().err

    def test_root_without_projects_subdir_rejected_exit_2(self, tmp_path, monkeypatch, capsys):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        bogus = tmp_path / "bogus"
        bogus.mkdir()
        with pytest.raises(SystemExit) as exc_info:
            _mod._resolve_cost_roots(_cost_args(extra_config_dirs=[str(bogus)]))
        assert exc_info.value.code == 2
        assert str(bogus) in capsys.readouterr().err

    def test_this_repo_and_config_dir_compose_returning_both_roots(
        self, tmp_path, monkeypatch, fake_config_dir_factory
    ):
        """--this-repo no longer refuses an explicit --config-dir extra:
        _iter_scoped_sessions matches slugs by basename and
        _path_to_project_slug derives them from `git worktree list` alone,
        both root-independent, so the refusal's own rationale ("--this-repo
        cannot filter a foreign config dir's worktrees") didn't match the
        mechanism. _resolve_cost_roots just returns the union of both roots;
        --this-repo's own filtering happens downstream in
        _resolve_project_scope."""
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        roots = _mod._resolve_cost_roots(_cost_args(this_repo=True, extra_config_dirs=[str(acct_b)]))
        assert roots == [default_dir / "projects", acct_b / "projects"]

    def test_no_redact_refused_with_multi_root(self, tmp_path, monkeypatch, capsys, fake_config_dir_factory):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        with pytest.raises(SystemExit) as exc_info:
            _mod._resolve_cost_roots(_cost_args(no_redact=True, extra_config_dirs=[str(acct_b)]))
        assert exc_info.value.code == 2
        assert "--no-redact" in capsys.readouterr().err

    def test_no_redact_allowed_alone_with_single_root(self, tmp_path, monkeypatch):
        """--no-redact with no --config-dir (single root) is unaffected."""
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        roots = _mod._resolve_cost_roots(_cost_args(no_redact=True))
        assert roots == [default_dir / "projects"]

    def test_summary_narrows_to_active_root_only_ignoring_declared_and_config_dir(
        self, tmp_path, monkeypatch, fake_config_dir_factory
    ):
        """Mechanism 1a: --summary resolves to config_dir()/projects alone,
        skipping both declared_transcript_roots() and --config-dir extras
        entirely -- --summary already refuses --config-dir in combination at
        _cost_report, so this only has to prove the union itself never
        forms."""
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        monkeypatch.setattr(_mod.scope, "declared_transcript_roots", lambda: [tmp_path / "declared-account"])
        acct_b = fake_config_dir_factory("acct-b")
        roots = _mod._resolve_cost_roots(_cost_args(summary=True, extra_config_dirs=[str(acct_b)]))
        assert roots == [default_dir / "projects"]

    def test_declared_roots_union_unaffected_for_non_cost_subcommand(self, tmp_path, monkeypatch):
        """Mechanism 1 narrows _resolve_cost_roots only for subcommand ==
        "cost" -- a populated declared-roots file still unions for every
        other _SUBCOMMANDS_WITH_OWN_CONFIG_DIR caller, since only cost's
        argparser defines --summary today and the gate is on `subcommand`,
        not on a bare summary_mode check."""
        active = tmp_path / "acct-a"
        (active / "projects").mkdir(parents=True)
        other = tmp_path / "acct-b"
        (other / "projects").mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(active))
        roots_file = tmp_path / "roots"
        roots_file.write_text(f"{other}\n")
        monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
        roots = _mod._resolve_cost_roots(_context_distribution_args(), subcommand="context-distribution")
        assert roots == [active / "projects", other / "projects"]


class TestCostMultiRootReport:
    """_cost_report's roots parameter: per-root scan diagnostics, the three
    distinct empty states, and the overlapping-root double-count guard."""

    def test_scan_summary_line_printed_per_root(self, tmp_path, capsys):
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out
        assert "cost: account-1: scanned 1 transcripts, 0 skipped (unreadable)" in out
        assert "cost: account-2: scanned 1 transcripts, 0 skipped (unreadable)" in out

    def test_this_repo_scan_count_reflects_repo_scope_not_whole_root(
        self, tmp_path, monkeypatch, capsys
    ):
        """--this-repo's diagnostic scan must count only the repo-scoped slug's
        transcripts, not _projects_glob's "*" fallback (always "*" under
        --this-repo) — otherwise a genuinely-empty repo-scoped result hides
        behind an unrelated project's nonzero count under the same root,
        reintroducing the silent-zero failure Step 8 exists to surface."""
        root = _write_cost_root(tmp_path, "acct-a", "-other-unrelated-project", "sess-a",
                                 [_priced("claude-sonnet-5", input=1_000_000)])
        # This repo's own slug dir exists but has zero transcripts — the
        # genuinely-empty case this diagnostic must not mask.
        (root / "-home-user-this-repo").mkdir()
        args = _cost_args(this_repo=True)
        args._this_repo_slugs = ["-home-user-this-repo"]
        monkeypatch.setattr(_mod.scope, "_repo_scoped_project_slugs", lambda *a, **k: args._this_repo_slugs)

        _mod._cost_report(args, date(2026, 8, 2), roots=[root])
        out = capsys.readouterr().out
        assert "cost: account-1: scanned 0 transcripts, 0 skipped (unreadable)" in out
        assert "WARNING: cost: account-1: no transcripts found for this scope" in out

    def test_permission_error_while_scanning_root_caught_and_reported_per_root(
        self, tmp_path, capsys
    ):
        """A real unreadable root must not propagate and abort the whole
        report — it's caught, reported for that root only (root_b's own scan
        and priced spend are unaffected), and the raw path embedded in
        str(exc) is suppressed under default redaction. Uses a genuine
        os.chmod'd directory rather than mocking _scan_root_transcripts —
        review found that pathlib.Path.glob silently swallows OSError while
        walking an unreadable directory rather than propagating it, so a
        mock-based test would pass even if the real permission-check path
        (os.access, in _scan_root_transcripts) were removed entirely."""
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        os.chmod(root_a, 0o000)
        try:
            _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        finally:
            os.chmod(root_a, 0o755)  # restore before tmp_path teardown

        captured = capsys.readouterr()
        out, err = captured.out, captured.err

        assert "cannot scan" in err
        assert str(root_a) not in err
        assert "cost: account-1: scanned 0 transcripts, 0 skipped (unreadable)" in out
        assert "WARNING: cost: account-1: no transcripts found for this scope" in out
        # occurrence=1: the global section, not one of the new per-account
        # "## Cost by account" sections this multi-root fixture also emits.
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True, occurrence=1)
        assert cols["$"] == "2.00"

    def test_empty_state_zero_transcripts_opened_warns_per_root_not_masked(self, tmp_path, capsys):
        """State (a)/(b): a root with no *.jsonl at all fires its own warning,
        not masked by a sibling root's non-zero scan — the original silent-
        zero bug wearing a per-root hat. account-N is assigned by resolved-path
        sort (_redaction_ordinals), not by roots= list order — "acct-b" sorts
        before "acct-empty", so root_b is account-1 despite being passed second."""
        empty_root = tmp_path / "acct-empty"
        empty_root.mkdir(parents=True)  # exists, but holds no project dirs at all
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[empty_root, root_b])
        out = capsys.readouterr().out
        assert "WARNING: cost: account-2: no transcripts found for this scope" in out
        assert "WARNING: cost: account-1" not in out

    def test_empty_state_project_dir_with_no_jsonl_also_warns(self, tmp_path, capsys):
        """State (b) specifically: a root whose project dir exists but holds
        no *.jsonl files at all — same warning predicate as an empty root."""
        root = tmp_path / "acct-a"
        (root / "-home-user-repo").mkdir(parents=True)
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root])
        out = capsys.readouterr().out
        assert "WARNING: cost: account-1: no transcripts found for this scope" in out

    def test_empty_state_transcripts_present_zero_priced_turns(self, fake_projects, capsys):
        """State (c): a transcript exists but carries no priced usage — the
        existing zero-state line, unchanged, and no scan warning (a
        transcript was found)."""
        _write_jsonl(fake_projects / "sess.jsonl", [_user_msg("hi")])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "(no priced turns in range)" in out
        assert "WARNING: cost:" not in out

    def test_empty_state_priced_spend_is_normal_report(self, fake_projects, capsys):
        """Priced spend: neither the scan warning nor the zero-state line appears."""
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "WARNING: cost:" not in out
        assert "(no priced turns in range)" not in out

    def test_three_empty_state_messages_are_textually_distinct(self, tmp_path, fake_projects, capsys):
        """Pins that Step 8's three states render different text — a single
        collapsed message would pass every test above individually but fail
        this one."""
        empty_root = tmp_path / "acct-empty"
        empty_root.mkdir(parents=True)
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[empty_root])
        zero_transcripts_out = capsys.readouterr().out

        _write_jsonl(fake_projects / "sess.jsonl", [_user_msg("hi")])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[fake_projects.parent])
        zero_priced_out = capsys.readouterr().out

        _write_jsonl(fake_projects / "sess2.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[fake_projects.parent])
        priced_out = capsys.readouterr().out

        assert "WARNING: cost:" in zero_transcripts_out
        assert "WARNING: cost:" not in zero_priced_out
        assert "WARNING: cost:" not in priced_out
        assert "(no priced turns in range)" in zero_priced_out
        assert "(no priced turns in range)" not in priced_out
        assert zero_transcripts_out != zero_priced_out != priced_out

    def test_same_project_slug_under_two_roots_sums_not_doubles(self, tmp_path, capsys):
        """Two distinct roots each hold a project dir with the identical slug
        name — genuinely different directories (different accounts), so both
        sessions count once each; the grand total is their sum, never
        doubled nor collapsed to just one."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])   # $2.00
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo", "sess-b",
                                   [_priced("claude-sonnet-5", input=2_000_000)])   # $4.00
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out
        # occurrence=1: the global section, not one of the new per-account
        # "## Cost by account" sections this multi-root fixture also emits.
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True, occurrence=1)
        assert cols["$"] == "6.00"

    def test_nested_root_alias_does_not_double_count(self, tmp_path, capsys):
        """One supplied root is a symlink, placed inside another root's own
        directory tree, that resolves back to that same root — nested inside
        another root as well as identical once resolved. The multi-root scan
        must dedupe by resolved real path so the grand total isn't doubled."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00
        nested_alias = root_a / "-home-user-repo" / "nested-alias-back-to-root-a"
        nested_alias.symlink_to(root_a)
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, nested_alias])
        out = capsys.readouterr().out
        # occurrence=1: the global section, not the new per-account "## Cost
        # by account" section this multi-root fixture also emits.
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True, occurrence=1)
        assert cols["$"] == "2.00"


class TestCostCorpusCoverageWarning:
    """--since window vs. a root's actual earliest turn: a root whose local
    corpus starts well after the requested window start must warn, not
    silently under-report -- one line per short root, never masked by a
    sibling root's full coverage."""

    def test_warning_fires_when_root_short_of_since_window(self, fake_projects, monkeypatch, capsys):
        fixed_now = 1_700_000_000.0
        monkeypatch.setattr(time, "time", lambda: fixed_now)
        since_ts = fixed_now - 5 * 86400
        earliest_ts = since_ts + 3 * 86400  # 3 days after window start, well over the 1-day threshold
        earliest_iso = datetime.fromtimestamp(earliest_ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000, ts=earliest_iso)])
        _mod._cost_report(_cost_args(since="5d"), date(2026, 8, 2), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "WARNING: cost: account-1: earliest turn found is" in out
        assert _mod._fmt_date(earliest_ts) in out
        assert _mod._fmt_date(since_ts) in out

    def test_warning_silent_when_root_fully_covers_window(self, fake_projects, monkeypatch, capsys):
        fixed_now = 1_700_000_000.0
        monkeypatch.setattr(time, "time", lambda: fixed_now)
        since_ts = fixed_now - 5 * 86400
        earliest_iso = datetime.fromtimestamp(since_ts - 86400, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000, ts=earliest_iso)])
        _mod._cost_report(_cost_args(since="5d"), date(2026, 8, 2), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "WARNING: cost:" not in out

    def test_warning_silent_when_since_not_given(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000, ts="2020-01-01T00:00:00.000Z"),
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "WARNING: cost:" not in out

    def test_boundary_exactly_24h_short_is_silent(self, fake_projects, monkeypatch, capsys):
        """Exactly 1 day after the window start is not "more than" 1 day
        short -- the inclusive edge of the no-warning side."""
        fixed_now = 1_700_000_000.0
        monkeypatch.setattr(time, "time", lambda: fixed_now)
        since_ts = fixed_now - 5 * 86400
        boundary_iso = datetime.fromtimestamp(since_ts + 86400, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000, ts=boundary_iso)])
        _mod._cost_report(_cost_args(since="5d"), date(2026, 8, 2), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "WARNING: cost:" not in out

    def test_boundary_24h_plus_epsilon_short_fires(self, fake_projects, monkeypatch, capsys):
        fixed_now = 1_700_000_000.0
        monkeypatch.setattr(time, "time", lambda: fixed_now)
        since_ts = fixed_now - 5 * 86400
        over_iso = datetime.fromtimestamp(since_ts + 86400 + 1, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000, ts=over_iso)])
        _mod._cost_report(_cost_args(since="5d"), date(2026, 8, 2), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "WARNING: cost: account-1: earliest turn found is" in out

    def test_two_root_case_names_short_root_not_masked_by_covered_root(self, tmp_path, monkeypatch, capsys):
        """acct-covered sorts before acct-short (resolved-path ordering), so
        acct-covered is account-1 and acct-short is account-2 -- the
        well-covered root's account-1 label must not appear in the warning."""
        fixed_now = 1_700_000_000.0
        monkeypatch.setattr(time, "time", lambda: fixed_now)
        since_ts = fixed_now - 5 * 86400
        covered_iso = datetime.fromtimestamp(since_ts - 86400, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        short_iso = datetime.fromtimestamp(since_ts + 3 * 86400, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        root_covered = _write_cost_root(tmp_path, "acct-covered", "-home-user-repo-a", "sess-a",
                                         [_priced("claude-sonnet-5", input=1_000, ts=covered_iso)])
        root_short = _write_cost_root(tmp_path, "acct-short", "-home-user-repo-b", "sess-b",
                                       [_priced("claude-sonnet-5", input=1_000, ts=short_iso)])
        _mod._cost_report(_cost_args(since="5d"), date(2026, 8, 2), roots=[root_covered, root_short])
        out = capsys.readouterr().out
        assert "WARNING: cost: account-2: earliest turn found is" in out
        assert "WARNING: cost: account-1:" not in out


class TestCostMultiRootRedaction:
    """Deny-case tests for cost's --config-dir redaction surface: no raw
    project label, config-dir path, or account-identifying directory name may
    appear anywhere in default-redacted multi-root stdout."""

    def test_default_redaction_hides_raw_labels_and_root_paths(self, tmp_path, capsys):
        root_a = _write_cost_root(tmp_path, "acct-alice-clientwork", "-home-user-repo-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        root_b = _write_cost_root(tmp_path, "acct-bob-clientwork", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out
        assert "repo-a" not in out
        assert "repo-b" not in out
        assert str(root_a) not in out
        assert str(root_b) not in out
        assert "acct-alice-clientwork" not in out
        assert "acct-bob-clientwork" not in out
        assert "account-1/private-project-1" in out
        assert "account-2/private-project-1" in out

    def test_single_root_subagent_only_project_gets_stable_label_not_miss_token(self, tmp_path, capsys):
        """A project whose main .jsonl is empty but whose only priced content
        lives in a subagent transcript still resolves to a real
        private-project-N label instead of tripping the redact-map-miss
        assertion — regression for the desync between
        _sorted_distinct_proj_labels' (main-thread-only) label census and
        cost's own subagent-merged session scan."""
        root = _write_cost_root(tmp_path, "acct-a", "-home-user-secret-clientname", "sess-a", [])
        _write_subagent_jsonl(root / "-home-user-secret-clientname", "sess-a", "agent-1",
                               [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root])
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Session", row_contains="session-1")
        assert cols["Proj"] == "private-project-1"
        assert _mod._REDACT_MAP_MISS_TOKEN not in out
        assert "-home-user-secret-clientname" not in out

    def test_multi_root_subagent_only_project_gets_stable_label_not_miss_token(self, tmp_path, capsys):
        """Multi-root counterpart, matching the original reported corpus
        shape: the subagent-only project's own root alongside a second,
        normal-project root."""
        root_a = _write_cost_root(tmp_path, "acct-alice-clientwork", "-home-user-secret-clientname", "sess-a", [])
        _write_subagent_jsonl(root_a / "-home-user-secret-clientname", "sess-a", "agent-1",
                               [_priced("claude-sonnet-5", input=1_000_000)])
        root_b = _write_cost_root(tmp_path, "acct-bob-clientwork", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        ordinal_a = _mod._redaction_ordinals([root_a, root_b])[root_a.resolve()]
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Session", row_contains="session-1")
        assert cols["Proj"] == f"account-{ordinal_a}/private-project-1"
        assert _mod._REDACT_MAP_MISS_TOKEN not in out
        assert "-home-user-secret-clientname" not in out

    def test_corpus_fingerprint_line_present_under_default_redaction(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[fake_projects.parent])
        out = capsys.readouterr().out
        assert "Corpus fingerprint:" in out

    def test_corpus_fingerprint_deterministic_for_same_label_set(self):
        """Same raw-label set, different key shapes (plain str vs. (root_idx,
        label) tuple) — the fingerprint hashes raw labels only, so the two
        must be equal."""
        flat_map = {"proj-a": "private-project-1", "proj-b": "private-project-2"}
        namespaced_map = {(0, "proj-a"): "account-1/private-project-1", (1, "proj-b"): "account-2/private-project-1"}
        assert _mod._corpus_fingerprint(flat_map) == _mod._corpus_fingerprint(namespaced_map)

    def test_corpus_fingerprint_differs_for_different_label_set(self):
        map_a = {"proj-a": "private-project-1"}
        map_b = {"proj-a": "private-project-1", "proj-b": "private-project-2"}
        assert _mod._corpus_fingerprint(map_a) != _mod._corpus_fingerprint(map_b)

    def test_no_redact_stamps_do_not_publish_banner_on_stdout_and_stderr(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(no_redact=True), date(2026, 8, 2))
        captured = capsys.readouterr()
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.out
        assert _mod._DO_NOT_PUBLISH_BANNER in captured.err

    def test_default_redact_omits_do_not_publish_banner(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        captured = capsys.readouterr()
        assert _mod._DO_NOT_PUBLISH_BANNER not in captured.out
        assert _mod._DO_NOT_PUBLISH_BANNER not in captured.err

    def test_redact_map_miss_raises_instead_of_printing_unmapped_row(self, tmp_path, monkeypatch):
        """A project label absent from the redact map (e.g. built over a
        stale roots list) is a hard error, never a printed 'unmapped' row —
        pins the fail-closed rewrite from _REDACT_MAP_MISS_TOKEN into an
        AssertionError for cost specifically."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.redaction, "_build_redact_map", lambda roots=None: {})
        with pytest.raises(AssertionError, match="redact map has no entry"):
            _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a])

    def test_redact_map_miss_assertion_omits_raw_label(self, tmp_path, monkeypatch):
        """The redact-map-miss hard-fail's own message must not embed the raw
        project label — main() has no top-level exception handler, so an
        uncaught AssertionError reaches stderr, and a raw label there would
        re-leak exactly what --redact exists to hide (a real gap found by
        review: the original message used {scoped_label!r})."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-secret-clientname", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.redaction, "_build_redact_map", lambda roots=None: {})
        with pytest.raises(AssertionError) as exc_info:
            _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a])
        assert "-home-user-secret-clientname" not in str(exc_info.value)

    def test_root_index_lookup_failure_omits_raw_path(self, tmp_path):
        """_root_index_for_path's own 'no known scan root' AssertionError is a
        structural sibling of the redact-map-miss assertion above — found by
        cumulative-diff review to still embed the raw jsonl path (f"cost:
        {jsonl} matched...") after the redact-map-miss sibling was fixed.
        Reproduces the real trigger: a project dir under a declared
        --config-dir root that is a symlink resolving OUTSIDE every declared
        root (not just aliasing another declared root, which is the already-
        covered, already-deduped case)."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        external = tmp_path / "external-unrelated-secret-clientname"
        external.mkdir()
        _write_jsonl(external / "sess-escaped.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        (root_b / "-home-user-escaped").symlink_to(external)

        with pytest.raises(AssertionError) as exc_info:
            _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        assert "external-unrelated-secret-clientname" not in str(exc_info.value)
        assert "-home-user-escaped" not in str(exc_info.value)

    def test_no_redact_refused_by_cost_report_itself_even_when_called_directly(self, tmp_path):
        """Defense-in-depth: _cost_report is the function that actually prints
        raw labels when redact is False, so it must refuse the multi-root +
        --no-redact combination itself rather than trusting that
        _resolve_cost_roots already validated it — every test in this class
        calls _cost_report directly, bypassing that CLI-level boundary."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_report(_cost_args(no_redact=True), date(2026, 8, 2), roots=[root_a, root_b])
        assert exc_info.value.code == 2

    def test_no_redact_refused_at_cmd_cost_when_config_dir_given(self, tmp_path, monkeypatch, capsys, fake_config_dir_factory):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        args = _cost_args(no_redact=True, extra_config_dirs=[str(acct_b)])
        with pytest.raises(SystemExit) as exc_info:
            _mod.cmd_cost(args)
        assert exc_info.value.code == 2
        assert "--no-redact" in capsys.readouterr().err

    def test_this_repo_end_to_end_reaches_report_with_config_dir(
        self, tmp_path, monkeypatch, capsys, fake_config_dir_factory
    ):
        """--config-dir + --this-repo end-to-end through cmd_cost: distinct
        from the _resolve_cost_roots-only allow-case above, since its own
        value is proving no path still reaches the report bypassing the
        (now-removed) guard."""
        default_dir = tmp_path / "default"
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        slug = "-home-user-this-repo"
        proj_default = default_dir / "projects" / slug
        proj_default.mkdir(parents=True)
        _write_jsonl(proj_default / "sess-default.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00

        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / slug
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [_priced("claude-sonnet-5", input=2_000_000)])  # $4.00

        monkeypatch.setattr(_mod.scope, "_repo_scoped_project_slugs", lambda *a, **k: [slug])

        args = _cost_args(this_repo=True, extra_config_dirs=[str(acct_b)])
        _mod.cmd_cost(args)

        out = capsys.readouterr().out
        assert "account-1/private-project-1" in out
        assert "account-2/private-project-1" in out
        # occurrence=1: the global section, not one of the new per-account
        # "## Cost by account" sections this multi-root fixture also emits.
        cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True, occurrence=1)
        assert cols["$"] == "6.00"


class TestCostByProject:
    """--by-project: per-(account root, project family) aggregation."""

    def test_by_project_flag_composes_with_this_repo_at_argparse_level(self):
        """--by-project is not in --this-repo/--projects' mutually exclusive
        group — both flags parse together instead of argparse rejecting the
        combination."""
        args = _mod.build_parser().parse_args(["cost", "--this-repo", "--by-project"])
        assert args.this_repo is True
        assert args.by_project is True

    def test_by_project_flag_composes_with_projects_at_argparse_level(self):
        args = _mod.build_parser().parse_args(["cost", "--projects", "foo", "--by-project"])
        assert args.projects == "foo"
        assert args.by_project is True

    def test_by_project_composes_with_this_repo_execution(self, tmp_path, monkeypatch, capsys):
        """End-to-end (not just argparse): --by-project + --this-repo runs
        _resolve_project_scope's repo-scoped iterator and still emits a
        per-project section for it."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "s.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(this_repo=True, by_project=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        section = out.split("## Cost by project")[1].split("## Top")[0]
        data_rows = [ln for ln in section.splitlines() if ln.strip() and not ln.strip().startswith("Project")]
        assert len(data_rows) == 1
        assert float(data_rows[0].split()[-2].replace(",", "")) == pytest.approx(2.00)

    def test_per_project_rows_sum_to_grand_total_across_multi_root_fixture(self, tmp_path, capsys):
        """Three sessions across two --config-dir roots: per-project rows'
        dollars sum to the report's own grand total, hand-computed."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a1",
                                   [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00
        _write_jsonl(root_a / "-home-user-repo-a" / "sess-a2.jsonl", [
            _priced("claude-sonnet-5", input=1_500_000),  # $3.00
        ])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b1",
                                   [_priced("claude-sonnet-5", input=2_000_000)])  # $4.00
        _mod._cost_report(_cost_args(by_project=True), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out

        grand_total = _extract_grand_total(out)
        assert grand_total == pytest.approx(9.00)  # 2.00 + 3.00 + 4.00, hand-computed

        section = out.split("## Cost by project")[1].split("## Top")[0]
        row_dollars = [
            float(ln.split()[-2].replace(",", ""))
            for ln in section.splitlines() if ln.strip().startswith("account-")
        ]
        assert len(row_dollars) == 2  # one row per (root, family)
        assert sum(row_dollars) == pytest.approx(grand_total)

    def test_multi_root_project_column_omits_redundant_account_prefix(self, tmp_path, capsys):
        """The Project column must not repeat the 'account-K/' prefix the
        adjacent Account column already carries — review found the redact
        map's raw namespaced value ('account-1/private-project-1') being
        printed verbatim into a row that already had 'account-1' as its own
        column."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(by_project=True), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out
        section = out.split("## Cost by project")[1].split("## Top")[0]
        data_rows = [ln for ln in section.splitlines() if ln.strip().startswith("account-")]
        assert len(data_rows) == 2
        for row in data_rows:
            assert "account-1/private-project-1" not in row
            assert "account-2/private-project-1" not in row
            assert "private-project-1" in row

    def test_worktree_suffixed_siblings_collapse_into_one_family_row(self, tmp_path, monkeypatch, capsys):
        """Two of this repo's own linked-worktree project dirs (sharing the
        base-repo-slug-plus---claude-worktrees-<branch> shape iter_sessions
        documents) aggregate into a single --by-project row instead of
        fragmenting one row per branch."""
        projects = tmp_path / "projects"
        proj_a = projects / "-home-user-testrepo--claude-worktrees-branch-a"
        proj_b = projects / "-home-user-testrepo--claude-worktrees-branch-b"
        proj_a.mkdir(parents=True)
        proj_b.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(proj_a / "sess-a.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00
        _write_jsonl(proj_b / "sess-b.jsonl", [_priced("claude-sonnet-5", input=500_000)])    # $1.00

        _mod._cost_report(_cost_args(by_project=True), date(2026, 8, 2))
        out = capsys.readouterr().out

        section = out.split("## Cost by project")[1].split("## Top")[0]
        data_rows = [ln for ln in section.splitlines() if ln.strip() and not ln.strip().startswith("Project")]
        assert len(data_rows) == 1  # not one row per worktree
        assert float(data_rows[0].split()[-2].replace(",", "")) == pytest.approx(3.00)

    def test_by_project_empty_corpus_renders_clean_zero_state(self, fake_projects, capsys):
        """No priced turns at all: the per-project section renders the same
        zero-state line as the top-N-sessions section, not a traceback or an
        empty table header with no explanation."""
        _mod._cost_report(_cost_args(by_project=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        section = out.split("## Cost by project")[1].split("## Top")[0]
        assert "(no priced turns in range)" in section

    def test_by_project_single_root_omits_raw_label_under_default_redaction(self, tmp_path, capsys):
        """General invariant, not just the prefix-specific one covered by
        test_multi_root_project_column_omits_redundant_account_prefix: the raw
        (pre-redaction) project directory name must be absent from
        --by-project output — the single-root branch has no account prefix
        to begin with, so it needs its own assertion of the general rule."""
        root = _write_cost_root(tmp_path, "acct-a", "-home-user-secret-clientname", "sess-a",
                                 [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(by_project=True), date(2026, 8, 2), roots=[root])
        out = capsys.readouterr().out
        section = out.split("## Cost by project")[1].split("## Top")[0]
        assert "-home-user-secret-clientname" not in section
        assert "private-project-1" in section

    def test_by_project_multi_root_omits_raw_label_under_default_redaction(self, tmp_path, capsys):
        """Same general invariant as the single-root test above, for the
        multi-root branch — distinct from the redundant-prefix-specific
        assertion in test_multi_root_project_column_omits_redundant_account_prefix."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-secret-clientname-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-secret-clientname-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(by_project=True), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out
        section = out.split("## Cost by project")[1].split("## Top")[0]
        assert "-home-user-secret-clientname-a" not in section
        assert "-home-user-secret-clientname-b" not in section

    def test_by_project_flag_off_omits_section_entirely(self, tmp_path, capsys):
        """--by-project defaults False; the '## Cost by project' header must
        not appear at all when the flag is omitted — pins the `if by_project:`
        guard against a future refactor hoisting the print unconditionally."""
        root = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a",
                                 [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root])
        out = capsys.readouterr().out
        assert "## Cost by project" not in out

    def test_non_worktree_label_colliding_with_suffix_shape_merges_into_existing_family(
        self, tmp_path, monkeypatch, capsys
    ):
        """_project_family matches on the literal '--claude-worktrees-'
        substring, not on genuine worktree provenance. Genuine collision: a
        project whose own (non-worktree) name happens to end in that exact
        substring strips down to the same family as an unrelated project
        that already has that name verbatim — merging two distinct projects'
        spend into one --by-project row. Pins the CURRENT (merging) behavior
        as a documented limitation, not a desired one — see
        _project_family's docstring."""
        projects = tmp_path / "projects"
        unrelated = projects / "-home-user-shared-family"
        coincidental = projects / "-home-user-shared-family--claude-worktrees-fake"
        unrelated.mkdir(parents=True)
        coincidental.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(unrelated / "sess-a.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])    # $2.00
        _write_jsonl(coincidental / "sess-b.jsonl", [_priced("claude-sonnet-5", input=500_000)])   # $1.00

        _mod._cost_report(_cost_args(by_project=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        section = out.split("## Cost by project")[1].split("## Top")[0]
        data_rows = [ln for ln in section.splitlines() if ln.strip() and not ln.strip().startswith("Project")]
        # Both derive to family "-home-user-shared-family" — one row, $3.00 total,
        # even though they are two genuinely unrelated project directories.
        assert len(data_rows) == 1
        assert float(data_rows[0].split()[-2].replace(",", "")) == pytest.approx(3.00)


class TestCostByAccount:
    """'## Cost by account': per-account token-class/model-ID breakdown,
    auto-shown under multi_root with no new flag (edit-format's own
    precedent), plus the third cross-check assertion guarding it."""

    def test_per_account_totals_sum_to_grand_total_across_multi_root_fixture(self, tmp_path, capsys):
        """Three sessions across two --config-dir roots: per-account
        token-class totals sum to the report's own grand total, hand-computed
        -- exercises the new per-account cross-check assertion's normal pass
        case, mirroring TestCostByProject's own sum-to-grand-total test."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a1",
                                   [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00
        _write_jsonl(root_a / "-home-user-repo-a" / "sess-a2.jsonl", [
            _priced("claude-sonnet-5", input=1_500_000),  # $3.00
        ])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b1",
                                   [_priced("claude-sonnet-5", input=2_000_000)])  # $4.00
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out

        grand_total = _extract_grand_total(out)
        assert grand_total == pytest.approx(9.00)  # 2.00 + 3.00 + 4.00, hand-computed

        account_totals = _extract_account_totals(out)
        assert len(account_totals) == 2
        assert sum(account_totals.values()) == pytest.approx(grand_total)
        assert account_totals[1] == pytest.approx(5.00)  # account-1 = acct-a: 2.00 + 3.00
        assert account_totals[2] == pytest.approx(4.00)  # account-2 = acct-b

    def test_per_account_specific_values_hand_computed(self, tmp_path, capsys):
        """Per-account class/model $ figures match a hand-computed value tied
        to that account's own fixture data -- not just that the two accounts'
        rows sum to the grand total, which would still pass even if the two
        accounts' dollars were swapped under the wrong ordinal."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00, all "input"
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-opus-5", input=500_000)])      # $2.50, all "input"
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out

        acct1_class = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True, occurrence=2)
        assert acct1_class["$"] == "2.00"
        acct2_class = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True, occurrence=3)
        assert acct2_class["$"] == "2.50"

        acct1_model = _table_cols(out, header_contains="Model", row_contains="claude-sonnet-5", occurrence=2)
        assert acct1_model["$"] == "2.00"
        acct2_model = _table_cols(out, header_contains="Model", row_contains="claude-opus-5", occurrence=3)
        assert acct2_model["$"] == "2.50"

    def test_per_account_missing_turn_raises_cross_check_assertion(self, tmp_path, monkeypatch):
        """Fault injection: skip the per-account accumulation for one turn
        while the global accumulators still see it -- proves the new
        per-account cross-check assertion actually fires, not just that it
        passes on correct code (mirrors the untested gap in the two
        pre-existing cross-checks, which also have no such test today)."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])

        original = _mod._accumulate_per_account_turn
        calls = {"n": 0}

        def flaky_accumulate(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return  # drop the first turn's per-account contribution
            return original(*args, **kwargs)

        monkeypatch.setattr(_mod.cost, "_accumulate_per_account_turn", flaky_accumulate)

        with pytest.raises(AssertionError, match="per-account"):
            _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])

    def test_cost_by_account_section_absent_at_single_root(self, fake_projects, capsys):
        """No --config-dir (single root): the '## Cost by account' section
        must not appear at all -- promoted from the plan's manual
        verification check into an automated regression pin."""
        _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "## Cost by account" not in out

    def test_per_account_zero_priced_account_renders_clean_zero_state(self, tmp_path, capsys):
        """One of two --config-dir roots has zero priced turns -- its own
        per-account section renders a clean $0.00/0.0% zero state, not a
        crash or malformed 0/0-share row -- mirrors edit-format's own
        up-front per_account initialization for every ordinal."""
        root_full = _write_cost_root(tmp_path, "acct-full", "-home-user-repo-a", "sess-a",
                                      [_priced("claude-sonnet-5", input=1_000_000)])
        root_zero = tmp_path / "acct-zero"
        (root_zero / "-home-user-repo-b").mkdir(parents=True)  # exists, no *.jsonl
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_full, root_zero])
        out = capsys.readouterr().out

        zero_class = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True, occurrence=3)
        assert zero_class["$"] == "0.00"
        assert zero_class["Share"] == "0.0%"

    def test_cost_by_account_negative_content_no_raw_project_labels(self, tmp_path, capsys):
        """The full multi-root report's stdout contains no raw project-label
        or config-dir-path substrings from the fixture roots. Also asserts
        the new '## Cost by account' section actually rendered -- a prior
        version of this test asserted only the negative and still passed
        with that entire section removed, since neither of its printers ever
        takes a project-label/path argument to begin with; pinning the
        section's presence ties the absence-of-leak claim to the code path
        it's meant to guard."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-secret-clientname-a", "sess-a",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-secret-clientname-b", "sess-b",
                                   [_priced("claude-opus-5", input=500_000)])
        _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        out = capsys.readouterr().out
        assert "## Cost by account" in out
        assert "### account-1" in out
        assert "### account-2" in out
        assert "-home-user-secret-clientname-a" not in out
        assert "-home-user-secret-clientname-b" not in out
        assert str(root_a) not in out
        assert str(root_b) not in out

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_permission_error_while_scanning_root_caught_and_reported_per_account_section(
        self, tmp_path, capsys
    ):
        """A real unreadable root among a multi-root scan must not leak its
        path via the new per-account section's presence/rendering, mirroring
        TestCostMultiRootReport's own chmod-based PermissionError test for
        the pre-existing global tables -- os.access's real permission check,
        not a mock, since pathlib.Path.glob silently swallows OSError while
        walking an unreadable directory rather than propagating it."""
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b",
                                   [_priced("claude-sonnet-5", input=1_000_000)])
        os.chmod(root_a, 0o000)
        try:
            _mod._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])
        finally:
            os.chmod(root_a, 0o755)  # restore before tmp_path teardown

        captured = capsys.readouterr()
        out, err = captured.out, captured.err

        assert "cannot scan" in err
        assert str(root_a) not in err
        assert "## Cost by account" in out
        assert "### account-1" in out
        assert "### account-2" in out
        zero_class = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True, occurrence=2)
        assert zero_class["$"] == "0.00"


class TestCostThreadSplit:
    """Cost by thread: main-thread vs. subagent (isSidechain) dollar split."""

    def test_main_and_subagent_dollars_sum_to_grand_total(self, fake_projects, capsys):
        session_id = "sess-thread"
        subagent_rec = _priced("claude-sonnet-5", input=500_000)  # subagent: $1.00
        subagent_rec["isSidechain"] = True
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000),  # main: $2.00
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [subagent_rec])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out

        grand_total = _extract_grand_total(out)
        assert grand_total == pytest.approx(3.00)  # 2.00 + 1.00, hand-computed

        thread_section = out.split("## Cost by thread")[1].split("## Top")[0]
        main_line = next(ln for ln in thread_section.splitlines() if ln.strip().startswith("main"))
        subagent_line = next(ln for ln in thread_section.splitlines() if ln.strip().startswith("subagent"))
        main_dollars = float(main_line.split()[-2].replace(",", ""))
        subagent_dollars = float(subagent_line.split()[-2].replace(",", ""))
        assert main_dollars == pytest.approx(2.00)
        assert subagent_dollars == pytest.approx(1.00)
        assert main_dollars + subagent_dollars == pytest.approx(grand_total)

    def test_subagent_dollars_not_misattributed_to_main_via_path_check(self, fake_projects, capsys):
        """Subagent records are merged into the parent session's record list
        by _read_session_file; iter_sessions yields only the main .jsonl
        path, which never contains 'subagents' in its parts. A
        `"subagents" in jsonl.parts`-style check on that yielded path would
        find no match and misattribute every dollar here to main; the
        isSidechain-based split must carry a nonzero subagent share instead."""
        session_id = "sess-path-check"
        main_jsonl = fake_projects / f"{session_id}.jsonl"
        subagent_rec = _priced("claude-sonnet-5", input=1_000_000)  # subagent: $2.00
        subagent_rec["isSidechain"] = True
        _write_jsonl(main_jsonl, [_priced("claude-sonnet-5", input=1_000_000)])  # main: $2.00
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [subagent_rec])
        assert "subagents" not in main_jsonl.parts  # the path a naive check would inspect

        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        thread_section = out.split("## Cost by thread")[1].split("## Top")[0]
        subagent_line = next(ln for ln in thread_section.splitlines() if ln.strip().startswith("subagent"))
        assert float(subagent_line.split()[-2].replace(",", "")) == pytest.approx(2.00)  # not 0.00

    def test_sidechain_fast_mode_multiplier_reflected_in_subagent_accumulator(self, fake_projects, capsys):
        """A speed:"fast" sidechain turn's 2x multiplier (already pinned at the
        unit level by TestPriceTurnSpeedGeoMultipliers) survives the isSidechain
        main/subagent split and stdout table rendering end-to-end."""
        session_id = "sess-sidechain-fast"
        subagent_rec = _priced("claude-sonnet-4-5", input=1_000_000, speed="fast")  # 2x: $6.00
        subagent_rec["isSidechain"] = True
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _priced("claude-sonnet-4-5", input=1_000_000),  # main: $3.00
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [subagent_rec])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out

        grand_total = _extract_grand_total(out)
        assert grand_total == pytest.approx(9.00)  # 3.00 + 6.00

        thread_section = out.split("## Cost by thread")[1].split("## Top")[0]
        main_line = next(ln for ln in thread_section.splitlines() if ln.strip().startswith("main"))
        subagent_line = next(ln for ln in thread_section.splitlines() if ln.strip().startswith("subagent"))
        assert float(main_line.split()[-2].replace(",", "")) == pytest.approx(3.00)
        assert float(subagent_line.split()[-2].replace(",", "")) == pytest.approx(6.00)  # not 3.00

    def test_print_thread_table_markdown_branch_renders_exact_gfm_lines(self, capsys):
        """Direct unit coverage of _print_thread_table's markdown branch,
        called with known args rather than relying solely on full-report
        integration coverage to catch a wiring bug (wrong argument order,
        wrong _pct_of denominator)."""
        _mod._print_thread_table(3.00, 1.00, 4.00, markdown=True)
        out = capsys.readouterr().out
        assert out == (
            "\n### Cost by thread\n\n"
            "| Thread | $ | Share |\n"
            "|---|---|---|\n"
            "| main | 3.00 | 75.0% |\n"
            "| subagent | 1.00 | 25.0% |\n"
        )


class TestCostMarkdownTablePrinters:
    """Direct unit coverage of _print_token_class_table's and
    _print_model_id_table's markdown branches, mirroring
    TestCostThreadSplit's _print_thread_table unit test -- pins each
    function's own formatting invariants without paying --summary's full
    _cost_report fixture cost."""

    def test_print_token_class_table_markdown_branch_renders_exact_gfm_lines(self, capsys):
        class_totals = {cls: 0.0 for cls in _mod._TOKEN_CLASSES}
        class_token_totals = {cls: 0 for cls in _mod._TOKEN_CLASSES}
        class_totals["input"] = 3.00
        class_token_totals["input"] = 1_500_000
        _mod._print_token_class_table(class_totals, class_token_totals, 3.00, markdown=True)
        out = capsys.readouterr().out
        assert out == (
            "### Cost by token class\n\n"
            "| Class | $ | Share | Tokens |\n"
            "|---|---|---|---|\n"
            "| cache_read | 0.00 | 0.0% | 0 |\n"
            "| cache_write_5m | 0.00 | 0.0% | 0 |\n"
            "| cache_write_1h | 0.00 | 0.0% | 0 |\n"
            "| output | 0.00 | 0.0% | 0 |\n"
            "| input | 3.00 | 100.0% | 1,500,000 |\n"
            "| **total** | **3.00** | | |\n"
        )

    def test_print_model_id_table_markdown_branch_renders_exact_gfm_lines(self, capsys):
        _mod._print_model_id_table({"claude-sonnet-5": 3.00, "claude-opus-5": 1.00}, 4.00, markdown=True)
        out = capsys.readouterr().out
        assert out == (
            "\n### Cost by model ID\n\n"
            "| Model | $ | Share |\n"
            "|---|---|---|\n"
            "| claude-sonnet-5 | 3.00 | 75.0% |\n"
            "| claude-opus-5 | 1.00 | 25.0% |\n"
        )


class TestCostBranchFilter:
    """--branches: per-record (not per-session) gitBranch filtering."""

    def test_branch_filter_is_per_record_not_per_session(self, fake_projects, capsys):
        """One session's records split across two branches: --branches A
        returns only A's dollars, and --branches A + --branches B sum to the
        unfiltered total — a session-level (not per-record) filter would
        misprice this in both directions (row2: one real session in this
        repo's own corpus splits 863 records on a feature branch, 212 on
        main)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),  # $2.00
            _priced("claude-sonnet-5", input=500_000, branch="main"),         # $1.00
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        unfiltered_total = _extract_grand_total(capsys.readouterr().out)

        _mod._cost_report(_cost_args(branches="feature-a"), date(2026, 8, 2))
        feature_total = _extract_grand_total(capsys.readouterr().out)
        assert feature_total == pytest.approx(2.00)

        _mod._cost_report(_cost_args(branches="main"), date(2026, 8, 2))
        main_total = _extract_grand_total(capsys.readouterr().out)
        assert main_total == pytest.approx(1.00)

        assert feature_total + main_total == pytest.approx(unfiltered_total)

    def test_branch_filter_accepts_comma_separated_list(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, branch="feature-a"),  # $2.00
            _priced("claude-sonnet-5", input=500_000, branch="feature-b"),    # $1.00
            _priced("claude-sonnet-5", input=250_000, branch="main"),         # $0.50
        ])
        _mod._cost_report(_cost_args(branches="feature-a,feature-b"), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert _extract_grand_total(out) == pytest.approx(3.00)

    def test_null_git_branch_record_counted_unfiltered_excluded_under_branch_filter(
        self, fake_projects, capsys
    ):
        """A null-gitBranch record is counted in an unfiltered run but
        excluded (deliberately) under any --branches filter — pins that the
        branch-filter sum invariant above isn't silently passing only because
        the fixture happens to have no null-branch record."""
        no_branch_rec = _priced("claude-sonnet-5", input=1_000_000)  # $2.00
        no_branch_rec["gitBranch"] = None
        _write_jsonl(fake_projects / "sess.jsonl", [
            no_branch_rec,
            _priced("claude-sonnet-5", input=500_000, branch="main"),  # $1.00
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        assert _extract_grand_total(capsys.readouterr().out) == pytest.approx(3.00)

        _mod._cost_report(_cost_args(branches="main"), date(2026, 8, 2))
        assert _extract_grand_total(capsys.readouterr().out) == pytest.approx(1.00)


class TestCostTokensColumn:
    """The 'Cost by token class' table's Tokens column, from _token_counts."""

    def test_tokens_column_hand_computed_totals(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced(
                "claude-sonnet-5",
                input=100_000, cache_read=200_000, ephemeral_1h=10_000, ephemeral_5m=20_000, output=5_000,
            ),
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        input_cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert int(input_cols["Tokens"].replace(",", "")) == 100_000
        cache_read_cols = _table_cols(out, header_contains="Class", row_contains="cache_read", row_startswith=True)
        assert int(cache_read_cols["Tokens"].replace(",", "")) == 200_000
        output_cols = _table_cols(out, header_contains="Class", row_contains="output", row_startswith=True)
        assert int(output_cols["Tokens"].replace(",", "")) == 5_000

    def test_tokens_column_excludes_unpriced_turns(self, fake_projects, capsys):
        """An unpriced model's tokens are excluded from the Tokens column —
        the same `dollars_by_class is None: continue` guard _price_turn's
        dollar accumulation already applies, so an unpriced turn's tokens are
        surfaced only via the separate unpriced-tokens counter, never folded
        into a total that looks complete."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("<synthetic>", input=1_000_000),   # unpriced
            _priced("claude-sonnet-5", input=100_000),  # priced
        ])
        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        out = capsys.readouterr().out
        input_cols = _table_cols(out, header_contains="Class", row_contains="input", row_startswith=True)
        assert int(input_cols["Tokens"].replace(",", "")) == 100_000  # not 1,100,000


class TestCostSummary:
    """--summary: a structurally scoped, aggregate-only rendering branch."""

    def test_summary_without_this_repo_exits_nonzero(self, fake_projects, capsys):
        with pytest.raises(SystemExit):
            _mod._cost_report(_cost_args(summary=True), date(2026, 8, 2))
        assert "--summary requires --this-repo" in capsys.readouterr().err

    def test_summary_with_explicit_default_projects_glob_exits_nonzero(self, fake_projects, capsys):
        """Even the literal default glob '*' is refused alongside --summary
        when --this-repo is absent — --summary does not accept --projects as
        an alternative scope gate at all, default value or otherwise."""
        with pytest.raises(SystemExit):
            _mod._cost_report(_cost_args(summary=True, projects="*"), date(2026, 8, 2))

    def test_summary_with_machine_wide_projects_bypass_glob_exits_nonzero(self, fake_projects, capsys):
        """row23: every _path_to_project_slug-derived project slug begins
        with '-', so a glob like '-*' is machine-wide despite not being the
        literal default '*' — pins that this bypass is refused too, not just
        the literal default value."""
        with pytest.raises(SystemExit):
            _mod._cost_report(_cost_args(summary=True, projects="-*"), date(2026, 8, 2))

    def test_summary_refuses_by_project_in_combination(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        (projects / "-repo-main").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            _mod._cost_report(_cost_args(summary=True, this_repo=True, by_project=True), date(2026, 8, 2))

    def test_summary_refuses_no_redact_in_combination(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        (projects / "-repo-main").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            _mod._cost_report(_cost_args(summary=True, this_repo=True, no_redact=True), date(2026, 8, 2))

    def test_summary_refuses_config_dir_in_combination(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        (projects / "-repo-main").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            _mod._cost_report(
                _cost_args(summary=True, this_repo=True, extra_config_dirs=["/somewhere"]),
                date(2026, 8, 2),
            )

    def test_summary_emits_nothing_identifying_and_excludes_cross_repo_spend(
        self, tmp_path, monkeypatch, capsys
    ):
        """Items 7 and 7a: a ≥2-project fixture (neither labelled
        claude-config, so the redact-map-miss path is exercisable if this
        path wrongly touched it). This repo's own raw label, the out-of-scope
        project's raw label, and every session ID are absent from --summary
        output; _redact_proj_label/_build_redact_map are never called on this
        path; and the out-of-scope project's dollars are excluded from the
        printed total, not just from the label set."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        other = projects / "-home-user-otherrepo"
        other.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "sess-mine.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])    # $2.00
        _write_jsonl(other / "sess-other.jsonl", [_priced("claude-sonnet-5", input=5_000_000)])  # $10.00
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        def _must_not_be_called(*a, **k):
            raise AssertionError("must not be called under --summary")
        monkeypatch.setattr(_mod.redaction, "_redact_proj_label", _must_not_be_called)
        monkeypatch.setattr(_mod.redaction, "_build_redact_map", _must_not_be_called)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2))
        out = capsys.readouterr().out

        assert "repo-main" not in out
        assert "otherrepo" not in out
        assert "sess-mine" not in out
        assert "sess-other" not in out
        assert "private-project" not in out
        assert _extract_md_grand_total(out) == pytest.approx(2.00)  # not 12.00 — other project's $10 excluded

    def test_summary_never_prints_session_or_project_sections(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "## Top" not in out
        assert "## Cost by project" not in out
        assert "## Cost by context-at-turn bucket" not in out
        assert "## Cost by account" not in out
        assert "1 priced sessions" in out
        assert "1 priced turns" in out

    def test_summary_unpriced_model_line_and_tokens_column_exclusion(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "sess.jsonl", [
            _priced("<synthetic>", input=1_000_000, output=500_000),  # unpriced
            _priced("claude-sonnet-5", input=100_000),                 # priced
        ])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        unpriced_tokens, unpriced_models = _extract_summary_unpriced(out)
        assert unpriced_tokens == 1_000_000 + 500_000
        assert unpriced_models == 1
        input_cols = _md_table_cols(out, header_contains="Class", row_contains="input")
        assert int(input_cols["Tokens"].replace(",", "")) == 100_000  # unpriced turn excluded

    def test_summary_unpriced_line_present_even_when_zero(self, tmp_path, monkeypatch, capsys):
        """Always prints the unpriced-tokens line, even at zero — an
        unrecognized model ID must never silently understate a published
        figure with no marker."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "sess.jsonl", [_priced("claude-sonnet-5", input=100_000)])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        unpriced_tokens, unpriced_models = _extract_summary_unpriced(out)
        assert unpriced_tokens == 0
        assert unpriced_models == 0

    def test_summary_stale_pricing_banner_present_past_expiry(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000)])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 11, 1))
        out = capsys.readouterr().out
        assert "STALE PRICING" in out

    def test_summary_stale_pricing_banner_absent_before_expiry(self, tmp_path, monkeypatch, capsys):
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        _write_jsonl(mine / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000)])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert "STALE PRICING" not in out

    def test_summary_totals_equal_active_root_only_when_second_account_declared(
        self, tmp_path, monkeypatch, capsys
    ):
        """Mechanism 1's core guarantee, driven through cmd_cost (not
        _cost_report directly) so _resolve_cost_roots' own narrowing is
        actually exercised: with two declared roots and a matching,
        nonzero-cost --this-repo project dir under each, cost --this-repo
        --branches main --summary's total equals the active root's total
        exactly -- the other declared account's $10.00 is excluded, not just
        unlabeled -- and the per-root scan line appears exactly once."""
        _two_declared_roots_with_this_repo_sessions(tmp_path, monkeypatch)
        _mod.cmd_cost(_cost_args(summary=True, this_repo=True, branches="main"))
        out = capsys.readouterr().out
        assert _extract_md_grand_total(out) == pytest.approx(2.00)  # not 12.00 -- other account excluded
        assert out.count("cost: account-1: scanned") == 1

    def test_without_summary_the_same_fixture_still_unions_both_accounts(
        self, tmp_path, monkeypatch, capsys
    ):
        """Allow-path counterpart: the identical two-declared-root fixture,
        the same command minus --summary, still unions both accounts' totals
        -- proves mechanism 1's narrowing is --summary-specific, not a
        blanket regression on --this-repo plus a declared root."""
        _two_declared_roots_with_this_repo_sessions(tmp_path, monkeypatch)
        _mod.cmd_cost(_cost_args(this_repo=True, branches="main"))
        out = capsys.readouterr().out
        assert _extract_grand_total(out) == pytest.approx(12.00)

    def test_summary_scope_line_states_single_account_and_that_dropping_flag_widens_it(
        self, tmp_path, monkeypatch, capsys
    ):
        """1c: the Scope: line must make it legible to a reader that dropping
        --summary from the printed command returns a different, larger
        total, not just state a transcript count."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        _write_jsonl(mine / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2), roots=[projects])
        out = capsys.readouterr().out
        assert (
            "Scope: this account only (1 transcripts scanned, 1 priced sessions, 1 priced turns)"
            " — dropping --summary reports every declared account too"
        ) in out

    def test_direct_cost_report_call_refuses_more_than_one_root_under_summary(
        self, tmp_path, capsys
    ):
        """Defense-in-depth (1b): _cost_report itself refuses summary_mode
        with more than one resolved root, since every direct caller
        (including this module's own tests) bypasses _resolve_cost_roots'
        CLI-level narrowing. The fixture clears both of _cost_report's
        earlier summary-mode exit-2 paths (this_repo=True, projects left at
        the accepted default "*", no by_project/no_redact/extra_config_dirs)
        so the new guard is the one that actually fires; asserting only the
        exit code would pass even with the new guard unimplemented, since
        either earlier refusal also exits 2 -- the guard's own distinct
        message is what proves it fired."""
        root_a = tmp_path / "acct-a" / "projects"
        root_a.mkdir(parents=True)
        root_b = tmp_path / "acct-b" / "projects"
        root_b.mkdir(parents=True)
        with pytest.raises(SystemExit) as exc_info:
            _mod._cost_report(
                _cost_args(summary=True, this_repo=True), date(2026, 8, 2), roots=[root_a, root_b],
            )
        assert exc_info.value.code == 2
        assert "refusing to report a multi-account total" in capsys.readouterr().err

    def test_summary_stdout_with_priced_data_never_leaks_a_home_rooted_path(
        self, tmp_path, monkeypatch, capsys
    ):
        """Shape-level redaction guard over the FULL --summary stdout: no
        /Users/, /home/, or the tmp_path fixture's own substring may appear
        -- a denylist of specific known strings wouldn't catch a new line
        added to this path later."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        _write_jsonl(mine / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2), roots=[projects])
        out = capsys.readouterr().out
        assert "/Users/" not in out
        assert "/home/" not in out
        assert str(tmp_path) not in out

    def test_summary_stdout_never_leaks_a_home_rooted_path_in_the_zero_transcripts_warning(
        self, tmp_path, monkeypatch, capsys
    ):
        """Same shape-level guard, exercising the WARNING: cost: account-N:
        no transcripts found ... line specifically -- it also reaches
        stdout (not stderr) under --summary and must pass the same guard,
        so this repo's own slug dir is left with zero transcripts."""
        projects = tmp_path / "projects"
        (projects / "-repo-main").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2), roots=[projects])
        out = capsys.readouterr().out
        assert "WARNING: cost: account-1: no transcripts found" in out
        assert "/Users/" not in out
        assert "/home/" not in out
        assert str(tmp_path) not in out

    def test_summary_model_id_markdown_table_well_formed_with_zero_transcripts(
        self, tmp_path, monkeypatch, capsys
    ):
        """A zero-transcript --summary run still renders a well-formed GFM
        model-ID table -- header and separator rows present even though no
        model ever priced a turn to produce a data row."""
        projects = tmp_path / "projects"
        (projects / "-repo-main").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2), roots=[projects])
        out = capsys.readouterr().out
        lines = out.splitlines()
        header_idx = next(i for i, ln in enumerate(lines) if ln == "| Model | $ | Share |")
        assert lines[header_idx + 1] == "|---|---|---|"
        assert lines[header_idx + 2].strip() == ""  # no data rows -- blank line ends the table

    def test_summary_token_class_total_row_renders_exact_bolded_gfm_shape(
        self, tmp_path, monkeypatch, capsys
    ):
        """The token-class table's closing row renders as the exact bolded
        GFM shape '| **total** | **X.XX** | | |' -- not just a numerically
        correct total, since a formatting regression (missing bold markers,
        wrong column count) would still pass a value-only pytest.approx
        check on the parsed number."""
        projects = tmp_path / "projects"
        mine = projects / "-repo-main"
        mine.mkdir(parents=True)
        _write_jsonl(mine / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])  # $2.00
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2), roots=[projects])
        out = capsys.readouterr().out
        # $2.00 -- claude-sonnet-5's $2/MTok input rate on 1M input tokens, hand-computed.
        assert "| **total** | **2.00** | | |" in out
        assert _extract_md_grand_total(out) == pytest.approx(2.00)

    def test_summary_title_line_is_bare_prose_not_a_markdown_heading(
        self, tmp_path, monkeypatch, capsys
    ):
        """The --summary title line renders as bare prose ('Cost summary
        (...)'), not a '## '-prefixed heading -- it sits inside the
        pr-description skill's own '## Cost' heading, so a '##' here would
        collide with that wrapper. A future edit re-adding '## ' would
        silently reintroduce that collision without this pin."""
        projects = tmp_path / "projects"
        (projects / "-repo-main").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", projects)
        monkeypatch.setattr(_mod.os, "getcwd", lambda: "/repo/main")

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["git", "worktree", "list"]:
                porcelain = "worktree /repo/main\nHEAD 0000\nbranch refs/heads/x\n"
                return subprocess.CompletedProcess(cmd, 0, porcelain, "")
            assert cmd == ["git", "rev-parse", "--show-toplevel"]
            return subprocess.CompletedProcess(cmd, 0, "/repo/main\n", "")
        monkeypatch.setattr(subprocess, "run", fake_run)

        _mod._cost_report(_cost_args(summary=True, this_repo=True), date(2026, 8, 2), roots=[projects])
        out = capsys.readouterr().out
        assert "\nCost summary (all time)\n" in out
        assert "## Cost summary" not in out


class TestCostWorktreeAgentBranchCarryForward:
    """A worktree-agent-* subagent record's branch is resolved by carry-
    forward against its own session's main-thread branch history, not
    excluded and not taken literally — see _session_branch_index and
    _attributed_branch."""

    def test_worktree_agent_record_folds_into_branch_active_at_dispatch_time(self, fake_projects, capsys):
        """(a): a main-thread record on the requested branch, followed (later
        timestamp) by a worktree-agent-* record — the subagent's dollars fold
        into the requested branch's headline total, not a separate line."""
        session_id = "sess-carry-a"
        main_rec = _priced(
            "claude-sonnet-5", input=1_000_000, branch="feature-a", ts="2026-08-01T10:00:00.000Z",
        )  # $2.00
        agent_rec = _priced(
            "claude-sonnet-5", input=500_000, branch="worktree-agent-abc123", ts="2026-08-01T11:00:00.000Z",
        )  # $1.00, later than main_rec
        agent_rec["isSidechain"] = True
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [main_rec])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [agent_rec])

        _mod._cost_report(_cost_args(branches="feature-a"), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert _extract_grand_total(out) == pytest.approx(3.00)  # 2.00 + 1.00 folded in

    def test_worktree_agent_record_before_any_main_thread_activity_falls_forward(self, fake_projects, capsys):
        """(b): the worktree-agent-* record's timestamp is *earlier* than any
        main-thread record in the session — resolves by falling forward to
        the index's earliest entry, same result as (a)."""
        session_id = "sess-carry-b"
        agent_rec = _priced(
            "claude-sonnet-5", input=500_000, branch="worktree-agent-abc123", ts="2026-08-01T09:00:00.000Z",
        )  # earlier than the only main-thread record
        agent_rec["isSidechain"] = True
        main_rec = _priced(
            "claude-sonnet-5", input=1_000_000, branch="feature-a", ts="2026-08-01T10:00:00.000Z",
        )
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [main_rec])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [agent_rec])

        _mod._cost_report(_cost_args(branches="feature-a"), date(2026, 8, 2))
        out = capsys.readouterr().out
        assert _extract_grand_total(out) == pytest.approx(3.00)

    def test_worktree_agent_record_resolves_through_mid_session_branch_switch(self, fake_projects, capsys):
        """(c) the discriminating case: main-thread records switch branches
        mid-session (feature-a, then later main), and the worktree-agent-*
        record's real timestamp falls *before* the switch — must resolve to
        the pre-switch (feature-a) branch. _read_session_file appends every
        subagent record after every main-thread record, so this record sits
        *after* both main-thread records in the merged list despite its
        earlier timestamp; a position- or last-branch-seen-based resolution
        would misresolve it to main instead of feature-a."""
        session_id = "sess-carry-c"
        first_main = _priced(
            "claude-sonnet-5", input=1_000_000, branch="feature-a", ts="2026-08-01T10:00:00.000Z",
        )  # $2.00
        second_main = _priced(
            "claude-sonnet-5", input=1_000_000, branch="main", ts="2026-08-01T12:00:00.000Z",
        )  # $2.00
        agent_rec = _priced(
            "claude-sonnet-5", input=500_000, branch="worktree-agent-abc123", ts="2026-08-01T11:00:00.000Z",
        )  # $1.00, between the two main-thread turns
        agent_rec["isSidechain"] = True
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [first_main, second_main])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [agent_rec])

        _mod._cost_report(_cost_args(branches="feature-a"), date(2026, 8, 2))
        feature_out = capsys.readouterr().out
        assert _extract_grand_total(feature_out) == pytest.approx(3.00)  # first_main + agent

        _mod._cost_report(_cost_args(branches="main"), date(2026, 8, 2))
        main_out = capsys.readouterr().out
        assert _extract_grand_total(main_out) == pytest.approx(2.00)  # second_main only

    def test_worktree_agent_record_unresolvable_with_no_main_thread_branch_in_session(
        self, fake_projects, capsys
    ):
        """(d): a session with no main-thread branch-bearing record at all —
        the worktree-agent-* record is counted in an unfiltered run but
        excluded from every --branches filter's total, the one case that
        stays genuinely unattributable (renders '?', GH-482's sentinel
        convention reused)."""
        session_id = "sess-carry-d"
        agent_rec = _priced("claude-sonnet-5", input=1_000_000, branch="worktree-agent-abc123")  # $2.00
        agent_rec["isSidechain"] = True
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [agent_rec])

        _mod._cost_report(_cost_args(), date(2026, 8, 2))
        assert _extract_grand_total(capsys.readouterr().out) == pytest.approx(2.00)

        _mod._cost_report(_cost_args(branches="main"), date(2026, 8, 2))
        assert _extract_grand_total(capsys.readouterr().out) == pytest.approx(0.0)


class TestCostTrend:
    def test_week_bucket_boundary_and_per_model_pricing(self, fake_projects, capsys):
        """Turns in two different ISO weeks land in separate rows, each priced
        against its own model's rate via _price_turn (Sonnet 5's $2/MTok base)."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),  # ISO 2026-W23
            _priced("claude-sonnet-5", input=2_000_000, ts="2026-06-08T10:00:00.000Z"),  # ISO 2026-W24
        ])
        _mod._cost_trend_report(_cost_trend_args(), date(2099, 1, 1))
        out = capsys.readouterr().out
        w23 = _extract_cost_trend_row(out, "2026-W23")
        w24 = _extract_cost_trend_row(out, "2026-W24")
        assert w23 is not None and w23["total"] == "2.00"
        assert w24 is not None and w24["total"] == "4.00"

    def test_opus_share_and_context_share_computed_per_week(self, fake_projects, capsys):
        """A week mixing an Opus-family turn with a Sonnet turn, and a >=200k
        context turn with a <200k turn, reports both shares correctly."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-opus-5", input=100_000, ts="2026-06-01T10:00:00.000Z"),          # $0.50, <200k
            _priced("claude-sonnet-5", input=100_000, ts="2026-06-01T11:00:00.000Z"),        # $0.20, <200k
            _priced("claude-sonnet-5", input=100_000, cache_read=100_000,
                    ts="2026-06-01T12:00:00.000Z"),  # $0.22 total, context 200,000 >=200k (inclusive edge)
        ])
        _mod._cost_trend_report(_cost_trend_args(), date(2099, 1, 1))
        out = capsys.readouterr().out
        row = _extract_cost_trend_row(out, "2026-W23")
        assert row is not None
        assert row["total"] == "0.92"
        assert row["opus_pct"] == "54.3%"
        assert row["context_pct"] == "23.9%"

    def test_current_week_labeled_partial_other_weeks_not(self, fake_projects, capsys):
        """The trailing bucket matching `today`'s ISO week is labeled '(partial)';
        an earlier, complete week is not."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),  # 2026-W23, complete
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-08T10:00:00.000Z"),  # 2026-W24, "today"'s week
        ])
        _mod._cost_trend_report(_cost_trend_args(), date(2026, 6, 8))  # today falls in 2026-W24
        out = capsys.readouterr().out
        assert "2026-W24 (partial)" in out
        assert "2026-W23 (partial)" not in out
        # The complete week's own row is unlabeled — its line starts with the bare week string.
        assert any(ln.startswith("2026-W23 ") and "(partial)" not in ln for ln in out.splitlines())

    def test_iso_year_boundary_dec31_and_jan1_share_correct_iso_week(self, fake_projects, capsys):
        """Dec 31 2025 falls in ISO week 2026-W01 — isocalendar() assigns
        year-end dates to the following calendar year's week numbering, which
        a bucket keyed on the datetime's plain `.year` would get wrong. `today`
        Jan 1 2026 is in the same ISO week, so the row is labeled '(partial)'."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2025-12-31T10:00:00.000Z"),
        ])
        _mod._cost_trend_report(_cost_trend_args(), date(2026, 1, 1))
        out = capsys.readouterr().out
        row = _extract_cost_trend_row(out, "2026-W01 (partial)")
        assert row is not None
        assert row["total"] == "2.00"
        assert "2025-W53" not in out

    def test_unpriced_model_turn_excluded_from_total_and_counted(self, fake_projects, capsys):
        """A turn from a model with no _MODEL_BASE_INPUT_RATES entry is
        excluded from every week's dollar total and reported via its own
        unpriced-turns counter, rather than silently dropped from the total."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
            _opus([{"type": "tool_use", "id": "r1", "name": "Read", "input": {}}], out=400,
                  ts="2026-06-01T11:00:00.000Z"),  # claude-opus-4-7 is deliberately unpriced
        ])
        _mod._cost_trend_report(_cost_trend_args(), date(2099, 1, 1))
        out = capsys.readouterr().out
        row = _extract_cost_trend_row(out, "2026-W23")
        assert row is not None and row["total"] == "2.00"
        # _opus()'s turn: input 50 + output 400 + cache_read 0 = 450 unpriced tokens.
        assert "1 unpriced turns / 450 tokens excluded from priced spend" in out

    def test_multi_record_request_id_group_priced_exactly_once(self, fake_projects, capsys):
        """Three assistant records sharing one requestId (one JSONL record per
        content block, as Claude Code writes for a single API call) carry a
        byte-identical usage dict — cost-trend prices the group's usage once,
        not once per record."""
        usage = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_input_tokens": 0}
        recs = [
            _asst("claude-sonnet-5", content=[{"type": "thinking", "thinking": "..."}],
                  ts="2026-06-01T10:00:00.000Z", request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
                  ts="2026-06-01T10:00:00.000Z", request_id="req-1"),
            _asst("claude-sonnet-5", content=[{"type": "text", "text": "done"}],
                  ts="2026-06-01T10:00:00.000Z", request_id="req-1"),
        ]
        for rec in recs:
            rec["message"]["usage"] = dict(usage)
        _write_jsonl(fake_projects / "sess.jsonl", recs)
        _mod._cost_trend_report(_cost_trend_args(), date(2099, 1, 1))
        out = capsys.readouterr().out
        row = _extract_cost_trend_row(out, "2026-W23")
        # $2.00 (claude-sonnet-5's $2/MTok input rate on 1M input tokens) once,
        # not $6.00 for pricing the group's usage three times over.
        assert row is not None and row["total"] == "2.00"


class TestCostTrendConfigDir:
    """cost-trend --config-dir: mirrors TestCostResolveRoots/TestCostMultiRootReport's
    shape for cost-trend's own wiring through _resolve_cost_roots."""

    def test_default_root_alone_when_no_extra_config_dirs(self, tmp_path, monkeypatch):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        roots = _mod._resolve_cost_roots(_cost_trend_args(), subcommand="cost-trend")
        assert roots == [default_dir / "projects"]

    def test_multiple_extra_config_dirs_appended_in_argument_order(
        self, tmp_path, monkeypatch, fake_config_dir_factory
    ):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        acct_c = fake_config_dir_factory("acct-c")
        roots = _mod._resolve_cost_roots(
            _cost_trend_args(extra_config_dirs=[str(acct_b), str(acct_c)]), subcommand="cost-trend"
        )
        assert roots == [default_dir / "projects", acct_b / "projects", acct_c / "projects"]

    def test_duplicate_root_deduped_by_resolve_not_string_equality(
        self, tmp_path, monkeypatch, fake_config_dir_factory
    ):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        roots = _mod._resolve_cost_roots(
            _cost_trend_args(extra_config_dirs=[str(acct_b), str(acct_b) + "/."]), subcommand="cost-trend"
        )
        assert roots == [default_dir / "projects", acct_b / "projects"]

    def test_nonexistent_root_rejected_exit_2(self, tmp_path, monkeypatch, capsys):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        missing = tmp_path / "does-not-exist"
        with pytest.raises(SystemExit) as exc_info:
            _mod._resolve_cost_roots(_cost_trend_args(extra_config_dirs=[str(missing)]), subcommand="cost-trend")
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert str(missing) in err
        # Subcommand label composes correctly -- distinct from _resolve_cost_roots'
        # own default "cost" label, which every TestCostResolveRoots case exercises.
        assert "cost-trend:" in err

    def test_root_without_projects_subdir_rejected_exit_2(self, tmp_path, monkeypatch, capsys):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        bogus = tmp_path / "bogus"
        bogus.mkdir()
        with pytest.raises(SystemExit) as exc_info:
            _mod._resolve_cost_roots(_cost_trend_args(extra_config_dirs=[str(bogus)]), subcommand="cost-trend")
        assert exc_info.value.code == 2
        assert "cost-trend:" in capsys.readouterr().err

    def test_this_repo_and_config_dir_compose_end_to_end(
        self, tmp_path, monkeypatch, capsys, fake_config_dir_factory
    ):
        """--config-dir + --this-repo end-to-end through cmd_cost_trend,
        mirroring TestCostMultiRootRedaction's own this-repo-composition test."""
        default_dir = tmp_path / "default"
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        slug = "-home-user-this-repo"
        proj_default = default_dir / "projects" / slug
        proj_default.mkdir(parents=True)
        _write_jsonl(proj_default / "sess-default.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),  # $2.00
        ])
        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / slug
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=2_000_000, ts="2026-06-01T10:00:00.000Z"),  # $4.00
        ])
        monkeypatch.setattr(_mod.scope, "_repo_scoped_project_slugs", lambda *a, **k: [slug])

        args = _cost_trend_args(this_repo=True, extra_config_dirs=[str(acct_b)])
        _mod.cmd_cost_trend(args)
        out = capsys.readouterr().out
        row = _extract_cost_trend_row(out, "2026-W23")
        assert row is not None and row["total"] == "6.00"

    def test_redaction_label_shape_account_prefix_no_raw_config_dir_path(
        self, tmp_path, monkeypatch, capsys, fake_config_dir_factory
    ):
        """The new per-root scan diagnostic labels each root account-N (not
        the raw config-dir path), matching cost's own labeling convention."""
        default_dir = tmp_path / "default"
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        proj_default = default_dir / "projects" / "-home-user-repo-a"
        proj_default.mkdir(parents=True)
        _write_jsonl(proj_default / "sess-a.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-repo-b"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod.cmd_cost_trend(_cost_trend_args(extra_config_dirs=[str(acct_b)]))
        out = capsys.readouterr().out
        assert "cost-trend: account-1: scanned 1 transcripts, 0 skipped (unreadable)" in out
        assert "cost-trend: account-2: scanned 1 transcripts, 0 skipped (unreadable)" in out
        assert str(default_dir) not in out
        assert str(acct_b) not in out

    def test_per_root_empty_window_warns(self, tmp_path, monkeypatch, capsys, fake_config_dir_factory):
        """One of two --config-dir roots holds no transcripts at all -- its
        own WARNING fires, not masked by the other root's non-empty scan
        (Mechanism 2 step 4's new per-root diagnostic, cost-trend's own
        counterpart to cost's own per-root-empty-state coverage)."""
        default_dir = tmp_path / "default"
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        (default_dir / "projects").mkdir(parents=True)  # exists, holds no project dirs
        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-repo-b"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod.cmd_cost_trend(_cost_trend_args(extra_config_dirs=[str(acct_b)]))
        out = capsys.readouterr().out
        # account-N is assigned by resolved-path sort (_redaction_ordinals),
        # not by scan order -- "acct-b" sorts before "default", so the empty
        # default root is account-2 despite being scanned first.
        assert "WARNING: cost-trend: account-2: no transcripts found for this scope" in out
        assert "WARNING: cost-trend: account-1" not in out

    def test_negative_content_no_raw_project_labels_in_stdout(
        self, tmp_path, monkeypatch, capsys, fake_config_dir_factory
    ):
        """Also asserts the new per-root scan-diagnostic lines actually
        printed -- a prior version of this test asserted only the negative
        and still passed with that entire diagnostic block removed, since it
        is the only new code in cost-trend capable of interpolating a path;
        pinning the diagnostic's presence ties the absence-of-leak claim to
        the code path it's meant to guard."""
        default_dir = tmp_path / "default"
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        proj_default = default_dir / "projects" / "-home-user-secret-clientname-a"
        proj_default.mkdir(parents=True)
        _write_jsonl(proj_default / "sess-a.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-secret-clientname-b"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        _mod.cmd_cost_trend(_cost_trend_args(extra_config_dirs=[str(acct_b)]))
        out = capsys.readouterr().out
        assert "cost-trend: account-1: scanned 1 transcripts, 0 skipped (unreadable)" in out
        assert "cost-trend: account-2: scanned 1 transcripts, 0 skipped (unreadable)" in out
        assert "-home-user-secret-clientname-a" not in out
        assert "-home-user-secret-clientname-b" not in out
        assert str(default_dir) not in out
        assert str(acct_b) not in out

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_permission_error_while_scanning_root_reported_without_raw_path(
        self, tmp_path, monkeypatch, capsys, fake_config_dir_factory
    ):
        """A real unreadable --config-dir root's diagnostic goes to stderr
        without the raw path -- cost-trend's own counterpart to cost's
        chmod-based PermissionError test. This is the one place in this
        diff's cost-trend code capable of interpolating a raw path (the
        `detail = str(exc) if not redact else ...` branch), and it had zero
        fixture coverage (positive or negative) before this test -- the
        prior negative-content test's fixtures never triggered a
        PermissionError, so the redact-suppression logic here was analyzed
        but never actually exercised."""
        default_dir = tmp_path / "default"
        monkeypatch.setattr(_mod.scope, "config_dir", lambda: default_dir)
        (default_dir / "projects").mkdir(parents=True)
        acct_b = fake_config_dir_factory("acct-b")
        proj_b = acct_b / "projects" / "-home-user-repo-b"
        proj_b.mkdir(parents=True)
        _write_jsonl(proj_b / "sess-b.jsonl", [
            _priced("claude-sonnet-5", input=1_000_000, ts="2026-06-01T10:00:00.000Z"),
        ])
        os.chmod(default_dir / "projects", 0o000)
        try:
            _mod.cmd_cost_trend(_cost_trend_args(extra_config_dirs=[str(acct_b)]))
        finally:
            os.chmod(default_dir / "projects", 0o755)  # restore before tmp_path teardown

        captured = capsys.readouterr()
        out, err = captured.out, captured.err
        assert "cannot scan" in err
        assert str(default_dir) not in err
        # account-N is assigned by resolved-path sort (_redaction_ordinals):
        # "acct-b" sorts before "default" under the same tmp_path parent, so
        # acct_b is account-1 despite default_dir being scanned first.
        assert "cost-trend: account-1: scanned 1 transcripts, 0 skipped (unreadable)" in out

    def test_no_redact_flag_not_registered(self, capsys):
        """Pins that cost-trend has no --no-redact argparse entry today, so
        `redact` in _cost_trend_report's diagnostic can never actually be
        False -- the raw-path branches it guards are dead code, not a live
        leak. This test exists so that adding --no-redact to cost-trend
        later (without deliberately revisiting that redaction logic) fails
        loudly here first, instead of silently reactivating a branch nothing
        else currently protects."""
        with pytest.raises(SystemExit):
            _mod.build_parser().parse_args(["cost-trend", "--no-redact"])
        err = capsys.readouterr().err
        assert "unrecognized arguments" in err
