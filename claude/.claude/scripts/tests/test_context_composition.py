"""Tests for transcript-analysis.py's context-composition subcommand.

Every fixture here is synthetic -- hand-built records, never sampled or copied from a real
transcript. See .claude/plans/context-composition-analyzer.md for the algorithm this pins.
"""
import importlib.util
import math
import re
import sys
from pathlib import Path

import pytest
from conftest import _agent_use, _asst, _bash_use, _tool_result, _user_msg, _write_jsonl

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
_spec = importlib.util.spec_from_file_location("transcript_analysis_context_composition", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


@pytest.fixture()
def fake_projects(tmp_path, monkeypatch):
    """Duplicated from test_transcript_analysis.py's own fixture of the same name -- only the
    six record-builder functions were extracted to conftest.py, not this fixture, so each test
    file keeps its own copy (this repo's DAMP-for-tests precedent)."""
    projects = tmp_path / "projects"
    proj = projects / "-home-user-testrepo"
    proj.mkdir(parents=True)
    monkeypatch.setattr(_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(_mod, "config_dir", lambda: tmp_path)
    return proj


@pytest.fixture()
def fake_config_dir_factory(tmp_path):
    def _make(name: str) -> Path:
        config_dir_path = tmp_path / name
        (config_dir_path / "projects").mkdir(parents=True)
        return config_dir_path
    return _make


def _context_composition_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    no_redact: bool = False,
    extra_config_dirs: list[str] | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "no_redact": no_redact,
        "extra_config_dirs": extra_config_dirs,
    })()


class TestReconciliationIdentity:
    def test_residual_recovers_hand_picked_static_prefix_constant(self):
        """The fixture's usage number (150) and its target static-prefix constant (50) are typed
        literals, independent of calling _READ_SCOPE_CHARS_PER_TOKEN on the fixture text -- only
        the 400-char user message's own estimate (400 // 4 = 100) is derived that way. Asserting
        the code recovers exactly 50 tests the reconciliation arithmetic, not a tautology built
        from the same formula on both sides."""
        records = [
            _user_msg("a" * 400, ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
        ]
        records[1]["message"]["usage"] = {"input_tokens": 150}  # hand-picked: 100 (item) + 50 (target)
        stats = _mod._scan_context_composition_session([records], None)
        assert stats["residuals"] == [[50]]


class TestReconciliationGateIsPerSequence:
    def test_two_individually_stable_sequences_with_different_baselines_do_not_trip_corpus_gate(self, capsys):
        """Two sequences, each individually perfectly stable (residual constant within itself)
        but at different static-prefix baselines (50 vs. 300) -- pooling their residuals corpus-
        wide would show mean=175, range=250, instability=250/175~=1.43 and wrongly refuse. The
        gate must instead take the worst PER-SEQUENCE instability (0.0 for both here)."""
        seq_a = [
            _user_msg("a" * 40, ts="2026-05-19T10:00:00.000Z"),  # 10 tok
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
        ]
        seq_a[1]["message"]["usage"] = {"input_tokens": 60}  # residual = 60 - 10 = 50
        seq_b = [
            _user_msg("b" * 40, ts="2026-05-19T10:00:00.000Z"),  # 10 tok
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
        ]
        seq_b[1]["message"]["usage"] = {"input_tokens": 310}  # residual = 310 - 10 = 300

        stats_a = _mod._scan_context_composition_session([seq_a], None)
        stats_b = _mod._scan_context_composition_session([seq_b], None)
        assert stats_a["residuals"] == [[50]]
        assert stats_b["residuals"] == [[300]]

        stats = _mod._new_context_composition_stats()
        _mod._merge_context_composition_stats(stats, stats_a)
        _mod._merge_context_composition_stats(stats, stats_b)

        _mod._print_context_composition_report(stats, "")
        out = capsys.readouterr().out
        assert "REFUSED" not in out


class TestRankingStability:
    def test_well_separated_categories_rank_correctly(self, capsys):
        """A 100-token item and a 5-token item -- grossly separated, so any reasonable rate
        weighting still ranks the larger one first. Reconciliation stays exactly flat (residual
        0 at every turn), so the refusal gate does not fire and the ranking prints."""
        records = [
            _user_msg([_tool_result("t1", "x" * 400)], ts="2026-05-19T10:00:00.000Z"),  # 100 tok, intro=0
            _asst(
                "claude-sonnet-5", content=[{"type": "text", "text": "z" * 20}],  # 5 tok, intro=1
                ts="2026-05-19T10:00:05.000Z",
            ),
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:10.000Z"),
        ]
        records[1]["message"]["usage"] = {
            "cache_creation_input_tokens": 100,
            "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 100},
        }
        records[2]["message"]["usage"] = {
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 5,
            "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 5},
        }
        stats = _mod._scan_context_composition_session([records], None)
        assert stats["residuals"] == [[0, 0]]

        big_category = f"{_mod._CATEGORY_TOOL_RESULT}:unknown"
        small_category = _mod._CATEGORY_ASSISTANT_TEXT
        # Direct comparison on the computed data, immune to report-formatting changes -- the
        # text-position check below additionally pins the printed report's own ranking order.
        assert stats["weighted_by_category"][big_category] > stats["weighted_by_category"][small_category]

        _mod._print_context_composition_report(stats, "")
        out = capsys.readouterr().out
        assert "REFUSED" not in out
        assert big_category in out and small_category in out
        assert out.index(big_category) < out.index(small_category), (
            "the 135-token-turn category must rank above the 6.25-token-turn one"
        )

    def test_adversarial_estimation_bias_trips_refusal_gate(self, capsys):
        """A dense-multibyte item whose real (usage-billed) token cost is ~3x its chars // 4
        estimate, introduced only on the second turn -- creating a jump in the per-turn residual
        (50 -> 250) rather than a uniformly-elevated-but-flat one, which is what actually trips
        range/mean instability (a bias present from turn 0 onward would just relabel part of the
        'static prefix' and stay flat). There is no separate ranking-stability mechanism to
        exercise -- this is the same reconciliation refusal gate the other tests in this class
        cover."""
        records = [
            _user_msg("hi", ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
            _user_msg("あ" * 400, ts="2026-05-19T10:00:10.000Z"),  # 400 chars -> 100 tok estimate
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:15.000Z"),
        ]
        records[1]["message"]["usage"] = {"input_tokens": 50}  # static prefix only; "hi" rounds to 0 est
        # Real tokenizer billed the CJK text at ~300 tokens (3x the chars // 4 estimate of 100).
        records[3]["message"]["usage"] = {"input_tokens": 350}  # 50 (static) + 300 (real CJK cost)
        stats = _mod._scan_context_composition_session([records], None)
        assert stats["residuals"] == [[50, 250]]

        _mod._print_context_composition_report(stats, "")
        out = capsys.readouterr().out
        assert "REFUSED" in out
        assert "## Category" not in out


class TestRefusalGate:
    def test_below_threshold_prints_ranking(self, capsys):
        stats = _mod._new_context_composition_stats()
        stats["residuals"] = [[100, 100]]  # instability 0.0, well under threshold
        stats["weighted_by_category"]["user_text"] = 42.0
        stats["item_counts"]["user_text"] = 1
        _mod._print_context_composition_report(stats, "")
        out = capsys.readouterr().out
        assert "REFUSED" not in out
        assert "user_text" in out

    def test_at_or_above_threshold_refuses(self, capsys):
        """Instability computed as exactly the threshold (0.5) still refuses -- the comparison
        is '>=', not '>'."""
        stats = _mod._new_context_composition_stats()
        stats["residuals"] = [[75, 125]]  # mean=100, spread=50, instability=50/100=0.5 -- exactly the threshold
        stats["weighted_by_category"]["user_text"] = 42.0
        _mod._print_context_composition_report(stats, "")
        out = capsys.readouterr().out
        assert "REFUSED" in out
        assert "## Category" not in out


class TestNegativeResidualInstability:
    def test_residual_instability_of_negative_residual_is_infinite(self):
        """A negative residual (context_at_turn undercounts the reconstructed resident size) is
        unconditionally maximally unstable, distinct from the finite range/mean value the formula
        would otherwise compute from these same two numbers."""
        assert _mod._context_composition_residual_instability([-5, 10]) == math.inf

    def test_negative_residual_from_real_sequence_triggers_refusal(self, capsys):
        """usage.input_tokens (10) undercounts the 100-tok item actually introduced that turn,
        producing residual = 10 - 100 = -90 -- a real scan (not a hand-built stats dict) must
        reach the same math.inf-driven refusal as the direct unit test above."""
        records = [
            _user_msg("a" * 400, ts="2026-05-19T10:00:00.000Z"),  # 100 tok estimate, intro=0
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
        ]
        records[1]["message"]["usage"] = {"input_tokens": 10}  # residual = 10 - 100 = -90
        stats = _mod._scan_context_composition_session([records], None)
        assert stats["residuals"] == [[-90]]

        _mod._print_context_composition_report(stats, "")
        out = capsys.readouterr().out
        assert "REFUSED" in out


class TestEmptyScan:
    def test_residual_instability_of_empty_sequence_is_zero(self):
        assert _mod._context_composition_residual_instability([]) == 0.0

    def test_report_on_fresh_stats_prints_nothing_scanned_without_raising(self, capsys):
        stats = _mod._new_context_composition_stats()
        _mod._print_context_composition_report(stats, "")
        out = capsys.readouterr().out
        assert "turns: 0 (nothing scanned)" in out
        assert "REFUSED" not in out
        assert "No priced turns in scope." in out


class TestCompaction:
    def test_item_residency_never_extends_past_eviction_turn(self):
        """A 2-turn sequence's own introduced-at-turn-0 item must be priced against that
        sequence's own last turn (index 1), never against a later sequence's turn indices --
        asserted by hand-computing the weighted total under the correct 2-turn closing index and
        showing it does not equal the value a compaction-blind 3-turn scan would produce."""
        seq1 = [
            _user_msg("p" * 40, ts="2026-05-19T10:00:00.000Z"),  # 10 tok, intro=0
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
            _user_msg("q" * 4, ts="2026-05-19T10:00:10.000Z"),
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:15.000Z"),
        ]
        seq1[1]["message"]["usage"] = {
            "cache_creation_input_tokens": 10,
            "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 10},
        }
        seq1[3]["message"]["usage"] = {"cache_read_input_tokens": 20, "cache_creation_input_tokens": 1}
        compact_boundary = {"type": "system", "subtype": "compact_boundary"}
        # seq2's own item uses a different category (tool_result) than seq1's plain user text,
        # so its own weighted contribution can't blend into the user_text assertion below.
        seq2 = [
            _user_msg([_tool_result("t2", "r" * 4)], ts="2026-05-19T10:00:20.000Z"),
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:25.000Z"),
        ]
        seq2[1]["message"]["usage"] = {"cache_creation_input_tokens": 1}

        group = [*seq1, compact_boundary, *seq2]
        stats = _mod._scan_context_composition_session([group], None)

        assert stats["sequences_scanned"] == 2
        assert stats["turns_scanned"] == 3  # 2 turns in seq1, 1 turn in seq2

        # Correct (compact_boundary-respecting): seq1's item p (10 tok, intro=0) is resident at
        # turns [0, 1] only -- last_turn(seq1)=1: read_span = read_mult[1] (=0.1), write_mult[0]
        # (eph_5m=10 -> base 1.25) => per_item = 1.35, weighted = 10 * 1.35 = 13.5. seq1's item q
        # (1 tok, intro=1=last_turn) contributes only its own write_mult[1] (flat
        # cache_creation_input_tokens=1 falls back to 5m-tier, base 1.25): weighted = 1.25.
        # A compaction-blind scan that folded seq2's turn into seq1 (closing index 2 instead of
        # 1) would instead give item p a read_span of read_mult[1]+read_mult[2]=0.2, i.e.
        # 10*1.45=14.5 -- 1 token-turn higher than the correct 13.5 asserted here.
        assert stats["weighted_by_category"]["user_text"] == pytest.approx(13.5 + 1.25)
        # And it must not leak into seq2's own (differently-categorized) item either.
        assert stats["weighted_by_category"][f"{_mod._CATEGORY_TOOL_RESULT}:unknown"] == pytest.approx(1.25)

    def test_mid_file_compact_boundary_splits_sequence(self):
        records = [
            _user_msg("a", ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
            {"type": "system", "subtype": "compact_boundary"},
            _user_msg("b", ts="2026-05-19T10:00:10.000Z"),
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:15.000Z"),
        ]
        records[1]["message"]["usage"] = {"input_tokens": 1}
        records[4]["message"]["usage"] = {"input_tokens": 1}
        sequences = _mod._split_context_sequences(records)
        assert len(sequences) == 2
        assert len(sequences[0]) == 2
        assert len(sequences[1]) == 2

    def test_large_context_drop_without_compact_boundary_is_not_misclassified(self):
        """Compaction detection is purely structural (the compact_boundary marker), never
        magnitude-based -- a huge turn-to-turn context drop with no such marker stays one
        sequence."""
        records = [
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
        ]
        records[0]["message"]["usage"] = {"input_tokens": 900_000}
        records[1]["message"]["usage"] = {"input_tokens": 100}
        sequences = _mod._split_context_sequences(records)
        assert len(sequences) == 1
        assert len(sequences[0]) == 2

    def test_issidechain_toggle_splits_sequence_on_the_legacy_embedded_shape(self):
        """A subagent dispatch embedded in the main file (isSidechain=true records interleaved,
        rather than split into its own subagents/*.jsonl) is a separate context window and must
        not be folded into the main thread's own turn indexing -- without this, a legacy-format
        embedded dispatch would corrupt the main sequence's introduction/closing turn math."""
        sidechain_user = _user_msg("b")
        sidechain_user["isSidechain"] = True
        sidechain_asst = _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:10.000Z")
        sidechain_asst["isSidechain"] = True
        sidechain_asst["message"]["usage"] = {"input_tokens": 5}

        records = [
            _user_msg("a", ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
            sidechain_user,
            sidechain_asst,
            _user_msg("c", ts="2026-05-19T10:00:15.000Z"),
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:20.000Z"),
        ]
        records[1]["message"]["usage"] = {"input_tokens": 10}
        records[5]["message"]["usage"] = {"input_tokens": 30}

        sequences = _mod._split_context_sequences(records)
        assert len(sequences) == 3
        assert [len(s) for s in sequences] == [2, 2, 2]
        assert not any(bool(rec.get("isSidechain")) for rec in sequences[0])
        assert all(bool(rec.get("isSidechain")) for rec in sequences[1])
        assert not any(bool(rec.get("isSidechain")) for rec in sequences[2])


class TestRequestIdMerge:
    def test_consecutive_same_request_id_assistant_records_merge_into_one_turn(self):
        """Mirrors the run-merge shape _dedup_turns_by_request_id relies on (see
        test_transcript_analysis.py's representative usage record at TestPriceTurnArity): two
        consecutive assistant records sharing one requestId are one API call, not two turns.
        Without the merge, turns_scanned would be 2 and only the first record's own content
        block would drive the residency/weighting math."""
        records = [
            _user_msg("hi", ts="2026-05-19T10:00:00.000Z"),
            _asst(
                "claude-sonnet-5", content=[{"type": "text", "text": "first block"}],
                request_id="req-1", ts="2026-05-19T10:00:05.000Z",
            ),
            _asst(
                "claude-sonnet-5", content=[{"type": "text", "text": "second block"}],
                request_id="req-1", ts="2026-05-19T10:00:06.000Z",
            ),
        ]
        records[1]["message"]["usage"] = {"input_tokens": 100}
        records[2]["message"]["usage"] = {"input_tokens": 100}  # identical input_tokens -- no drift
        stats = _mod._scan_context_composition_session([records], None)
        assert stats["turns_scanned"] == 1
        # Both content blocks landed in the merged turn's content, not just the first record's.
        assert stats["item_counts"][_mod._CATEGORY_ASSISTANT_TEXT] == 2


class TestSinceCutoff:
    def test_item_introduced_before_cutoff_read_after_excludes_only_the_pre_cutoff_turn(self):
        """Item introduced at turn0 (before --since), read at turn1 (after --since): the excluded
        turn's own write contribution drops out of weighting while the later in-window read still
        counts, and residuals/reconciliation (turn-level bookkeeping, not rate-weighting) stay
        unaffected by the exclusion."""
        records = [
            _user_msg("a" * 40, ts="2026-05-19T09:00:00.000Z"),  # 10 tok, intro=0, before cutoff
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T09:00:05.000Z"),  # turn0, before cutoff
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),  # turn1, after cutoff
        ]
        records[1]["message"]["usage"] = {
            "cache_creation_input_tokens": 10,
            "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 10},
        }
        records[2]["message"]["usage"] = {"cache_read_input_tokens": 10}
        since_ts = _mod._parse_ts("2026-05-19T09:30:00.000Z")
        stats = _mod._scan_context_composition_sequence(records, since_ts)

        assert stats["since_excluded_turns"] == 1  # turn0 only
        # write_mult[0] excluded (0.0); read_mult[1] in-window (0.1) -- a bug that failed to
        # zero the pre-cutoff write term would give 10*(0.1+1.25)=13.5 instead of 1.0.
        assert stats["weighted_by_category"]["user_text"] == pytest.approx(1.0)
        assert stats["residuals"] == [[0, 0]]


class TestLastTurnResidency:
    def test_item_introduced_on_sequences_final_turn_has_zero_subsequent_turns(self):
        """An item generated as the assistant's OWN output on the sequence's last turn is
        introduced at last_turn + 1 -- a turn that does not exist -- so it never becomes resident
        and contributes nothing, distinct from an item introduced strictly AT the last turn
        (which contributes its write-rate term alone; see the compaction test above for that
        case). Both must be asserted explicitly since only the second one prices anything."""
        records = [
            _user_msg("a", ts="2026-05-19T10:00:00.000Z"),
            _asst(
                "claude-sonnet-5", content=[{"type": "text", "text": "never resident"}],
                ts="2026-05-19T10:00:05.000Z",
            ),
        ]
        records[1]["message"]["usage"] = {"input_tokens": 1}
        stats = _mod._scan_context_composition_session([records], None)
        # The assistant's own text block is classified (counted) but contributes zero weighted
        # token-turns -- it was generated on the sequence's only/last turn and never sent back.
        assert stats["item_counts"][_mod._CATEGORY_ASSISTANT_TEXT] == 1
        assert _mod._CATEGORY_ASSISTANT_TEXT not in stats["weighted_by_category"]


class TestRateWeighting:
    def test_two_rate_classes_equal_raw_token_turns_weight_differently(self):
        """Two items, each resident for exactly one turn (its own introduction/write turn, size
        10 tokens each -- equal raw token-turns), but one turn's cache-write is 1h-tier (2x) and
        the other's is 5m-tier (1.25x). Equal raw size and turn count, different rate class,
        different weighted total -- in the expected direction (1h > 5m)."""
        # Each item is a user message introduced right before the sequence's only (and
        # therefore last) turn, so intro == last_turn and it prices at the write rate alone.
        seq_1h = [
            _user_msg("a" * 40, ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
        ]
        seq_1h[1]["message"]["usage"] = {
            "cache_creation_input_tokens": 10,
            "cache_creation": {"ephemeral_1h_input_tokens": 10, "ephemeral_5m_input_tokens": 0},
        }
        seq_5m = [
            _user_msg("b" * 40, ts="2026-05-19T10:00:00.000Z"),
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
        ]
        seq_5m[1]["message"]["usage"] = {
            "cache_creation_input_tokens": 10,
            "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 10},
        }
        stats_1h = _mod._scan_context_composition_session([seq_1h], None)
        stats_5m = _mod._scan_context_composition_session([seq_5m], None)
        weighted_1h = stats_1h["weighted_by_category"][_mod._CATEGORY_USER_TEXT]
        weighted_5m = stats_5m["weighted_by_category"][_mod._CATEGORY_USER_TEXT]
        assert weighted_1h == pytest.approx(10 * 2.0)
        assert weighted_5m == pytest.approx(10 * 1.25)
        assert weighted_1h > weighted_5m

    def test_mixed_class_turn_prices_per_item_not_one_blended_rate(self):
        """Item A (user text, 10 tok) is introduced at turn0 (cache-write, both TTL tiers present
        at once -- mirrors test_transcript_analysis.py's own 'representative usage record'
        shape), then read at turns 1 and 2. Item B (a tool_result, 20 tok) is introduced at turn1
        -- the turn that ALSO carries item A's cache-read, genuinely mixing read and write
        classes on one turn's usage. Asserting each item's own weighted total separately (not
        their sum) is what actually exercises the (item, turn) keying; a single blended per-turn
        rate could not produce these two different numbers from one turn's usage."""
        records = [
            _user_msg("a" * 40, ts="2026-05-19T10:00:00.000Z"),  # 10 tok, intro=0
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
            _user_msg([_tool_result("t1", "b" * 80)], ts="2026-05-19T10:00:10.000Z"),  # 20 tok, intro=1
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:15.000Z"),
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:20.000Z"),
        ]
        records[1]["message"]["usage"] = {
            "cache_creation_input_tokens": 15,
            "cache_creation": {"ephemeral_1h_input_tokens": 10, "ephemeral_5m_input_tokens": 5},
        }
        records[3]["message"]["usage"] = {
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 8,
            "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 8},
        }
        records[4]["message"]["usage"] = {"cache_read_input_tokens": 18}

        stats = _mod._scan_context_composition_session([records], None)

        # Item A: last_turn=2. write_mult[0] = (10*2 + 5*1.25) / 15 = 1.75. read_span =
        # read_mult[1] + read_mult[2] = 0.1 + 0.1 = 0.2. per_item = 1.95. weighted = 10*1.95=19.5.
        assert stats["weighted_by_category"]["user_text"] == pytest.approx(19.5)

        # Item B: last_turn=2. write_mult[1] = (0*2 + 8*1.25)/8 = 1.25. read_span =
        # read_mult[2] = 0.1. per_item = 1.35. weighted = 20*1.35 = 27.0.
        assert stats["weighted_by_category"][f"{_mod._CATEGORY_TOOL_RESULT}:unknown"] == pytest.approx(27.0)

    def test_blended_write_tier_isolated_via_zero_read_span(self):
        """Same blended-tier usage as item A above (both eph_1h and eph_5m nonzero on one turn),
        but the item is introduced on the sequence's own (only) last turn, so read_span is 0 --
        the same read_span=0 isolation technique test_two_rate_classes_equal_raw_token_turns_
        weight_differently uses for a pure tier, applied here to the blend scalar directly."""
        records = [
            _user_msg("a" * 40, ts="2026-05-19T10:00:00.000Z"),  # 10 tok, intro=0 == last_turn
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
        ]
        records[1]["message"]["usage"] = {
            "cache_creation_input_tokens": 15,
            "cache_creation": {"ephemeral_1h_input_tokens": 10, "ephemeral_5m_input_tokens": 5},
        }
        stats = _mod._scan_context_composition_session([records], None)
        # write_mult[0] = (10*2 + 5*1.25) / 15 = 1.75, read_span = 0 (no turn after intro).
        assert stats["weighted_by_category"]["user_text"] == pytest.approx(10 * 1.75)

    def test_zero_cache_write_tokens_prices_at_input_rate_not_5m_write_tier(self):
        """A turn that introduces new content but records zero cache-write tokens (both
        cache_creation and the flat cache_creation_input_tokens absent) is a normal shape between
        cache-control breakpoints -- _price_turn's own rate for it is the plain input rate (1x),
        never the 5m cache-write tier (1.25x)."""
        records = [
            _user_msg("a" * 40, ts="2026-05-19T10:00:00.000Z"),  # 10 tok, intro=0 == last_turn
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
        ]
        records[1]["message"]["usage"] = {"input_tokens": 100}  # no cache_creation fields at all
        stats = _mod._scan_context_composition_session([records], None)
        # write_mult[0] = 1.0 (plain input rate); a bug using the 5m-tier fallback would give 12.5.
        assert stats["weighted_by_category"]["user_text"] == pytest.approx(10.0)


class TestIntroducedVsResidentSplit:
    def test_split_matches_usage_class_split_when_estimates_are_accurate(self):
        """usage.input_tokens is set to exactly the item's own chars // 4 estimate (100), with
        no separate static-prefix component, so actual_new (context_at_turn - cache_read) and
        our own introduced-size bookkeeping agree exactly."""
        records = [
            _user_msg("a" * 400, ts="2026-05-19T10:00:00.000Z"),  # 100 tok
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
        ]
        records[1]["message"]["usage"] = {"input_tokens": 100}
        stats = _mod._scan_context_composition_session([records], None)
        assert stats["introduced_size_total"] == stats["actual_new_size_total"] == 100

    def test_persistent_mismatch_surfaces_in_report_as_ambiguous_not_as_a_rate_bug(self, capsys):
        """Both turns' usage attributes its own genuinely-new content entirely to cache_read
        (implausible, but a clean way to construct a divergence) while the TOTAL context_at_turn
        each turn still matches our resident-size bookkeeping exactly -- so reconciliation stays
        perfectly flat (residual 50 at both turns, no refusal) while the introduced-vs-resident
        split diverges sharply. This isolates the split diagnostic from the refusal gate."""
        records = [
            _user_msg("a" * 40, ts="2026-05-19T10:00:00.000Z"),  # 10 tok, intro=0
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
            _user_msg("b" * 40, ts="2026-05-19T10:00:10.000Z"),  # 10 tok, intro=1
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:15.000Z"),
        ]
        records[1]["message"]["usage"] = {
            "cache_read_input_tokens": 55, "cache_creation_input_tokens": 5,
            "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 5},
        }
        records[3]["message"]["usage"] = {
            "cache_read_input_tokens": 65, "cache_creation_input_tokens": 5,
            "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 5},
        }
        stats = _mod._scan_context_composition_session([records], None)
        assert stats["residuals"] == [[50, 50]]
        assert stats["introduced_size_total"] == 20
        assert stats["actual_new_size_total"] == 10

        _mod._print_context_composition_report(stats, "")
        out = capsys.readouterr().out
        assert "REFUSED" not in out
        assert "ambiguous" in out
        # Hedged, not asserted as a definitive rate-classification bug: a mismatch here cannot
        # by itself distinguish a wrong write-timing rule from chars//4 estimation bias that
        # correlates with introduced-vs-resident content.
        assert "not necessarily a rate-classification bug" in out


class TestFastModeAndGeoWeighting:
    def test_fast_and_geo_scale_apply_at_their_own_turn_not_uniformly(self):
        """A single item written on an ordinary turn (no fast/geo), then read on a fast-mode
        turn and a US-inference-geo turn -- confirming the scale factor is looked up per turn
        (see _context_composition_turn_rate_scale), not applied once to the whole item."""
        records = [
            _user_msg("a" * 40, ts="2026-05-19T10:00:00.000Z"),  # 10 tok, intro=0
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:10.000Z"),
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:15.000Z"),
        ]
        records[1]["message"]["usage"] = {
            "cache_creation_input_tokens": 1,
            "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 1},
        }
        records[2]["message"]["usage"] = {"cache_read_input_tokens": 1, "speed": "fast"}
        records[3]["message"]["usage"] = {"cache_read_input_tokens": 1, "inference_geo": "us"}

        stats = _mod._scan_context_composition_session([records], None)
        # write_mult[0] = 1.25 (5m tier, no scale). read_mult[1] = 0.1 * 2 (fast) = 0.2.
        # read_mult[2] = 0.1 * 1.1 (us geo) = 0.11. per_item = 1.25 + 0.2 + 0.11 = 1.56.
        # weighted = 10 * 1.56 = 15.6 -- a bug that dropped the scale would give 10*1.45=14.5.
        assert stats["weighted_by_category"]["user_text"] == pytest.approx(15.6)

    def test_fast_and_geo_scale_multiply_together_on_one_turn(self):
        """Both usage.speed="fast" and usage.inference_geo="us" set on the SAME turn --
        _context_composition_turn_rate_scale multiplies the two factors (2 * 1.1 = 2.2x), not
        just applies whichever check happens to run second."""
        records = [
            _user_msg("a" * 40, ts="2026-05-19T10:00:00.000Z"),  # 10 tok, intro=0
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:05.000Z"),
            _asst("claude-sonnet-5", content=[], ts="2026-05-19T10:00:10.000Z"),
        ]
        records[1]["message"]["usage"] = {
            "cache_creation_input_tokens": 1,
            "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 1},
        }
        records[2]["message"]["usage"] = {"cache_read_input_tokens": 1, "speed": "fast", "inference_geo": "us"}

        stats = _mod._scan_context_composition_session([records], None)
        # write_mult[0] = 1.25 (5m tier, no scale). read_mult[1] = 0.1 * 2 * 1.1 = 0.22 (combined).
        # per_item = 1.25 + 0.22 = 1.47. weighted = 10 * 1.47 = 14.7 -- a bug that applied only one
        # factor would give 10*(1.25+0.2)=14.5 (fast alone) or 10*(1.25+0.11)=13.6 (geo alone).
        assert stats["weighted_by_category"]["user_text"] == pytest.approx(14.7)


class TestRedaction:
    _FAKE_PATH = "/Users/<fakeoperator>/secret-project-x/config.py"
    _FAKE_SESSION_UUID = "deadbeef1234feed"

    def _seed_needle_session(self, proj: Path) -> None:
        records = [
            _user_msg(f"read {self._FAKE_PATH}", ts="2026-05-19T10:00:00.000Z"),
            _asst(
                "claude-sonnet-5",
                content=[
                    _bash_use("t1", f"cat {self._FAKE_PATH}"),
                    {"type": "text", "text": f"session {self._FAKE_SESSION_UUID} looked at this"},
                ],
                ts="2026-05-19T10:00:05.000Z",
            ),
            _user_msg([_tool_result("t1", f"contents of {self._FAKE_PATH}")], ts="2026-05-19T10:00:10.000Z"),
        ]
        records[1]["message"]["usage"] = {"input_tokens": 1}
        _write_jsonl(proj / f"{self._FAKE_SESSION_UUID}.jsonl", records)

    def test_needle_absent_from_default_redacted_output(self, fake_projects, capsys):
        self._seed_needle_session(fake_projects)
        _mod._context_composition_report(_context_composition_args())
        combined = "".join(capsys.readouterr())
        assert self._FAKE_PATH not in combined
        assert self._FAKE_SESSION_UUID not in combined

    def test_needle_absent_under_no_redact_single_root(self, fake_projects, capsys):
        """--no-redact prints the DO NOT PUBLISH banner but the report itself carries no
        text content to leak in the first place -- the needle stays absent either way."""
        self._seed_needle_session(fake_projects)
        _mod._context_composition_report(_context_composition_args(no_redact=True))
        combined = "".join(capsys.readouterr())
        assert _mod._DO_NOT_PUBLISH_BANNER in combined
        assert self._FAKE_PATH not in combined
        assert self._FAKE_SESSION_UUID not in combined

    def test_redact_ordinal_label_appears_and_real_root_path_absent_under_default_redaction(
        self, fake_projects, capsys
    ):
        """Mirrors cost's own account-N diagnostic assertions (test_transcript_analysis.py
        TestCostMultiRootReport, e.g. :6419) -- the per-root scan-diagnostic label is the only
        place `redact` actually changes context-composition's printed output (root_label =
        "account-N" if redact else str(root.parent)); every other TestRedaction test here would
        still pass even if that branch were deleted, since item text is discarded structurally
        regardless of `redact`."""
        self._seed_needle_session(fake_projects)
        real_root_parent = str(fake_projects.parent.parent)  # config_dir(), the segment redact hides
        _mod.cmd_context_composition(_context_composition_args())
        out = capsys.readouterr().out
        assert "context-composition: account-1: scanned" in out
        assert real_root_parent not in out

    def test_real_root_path_appears_and_account_label_absent_under_no_redact(self, fake_projects, capsys):
        """The other arm of the same ternary as the test above -- with --no-redact at single-root
        scope (opt-in, matching context-distribution's already-shipped behavior), the real path
        prints and the account-N label does not."""
        self._seed_needle_session(fake_projects)
        real_root_parent = str(fake_projects.parent.parent)
        _mod.cmd_context_composition(_context_composition_args(no_redact=True))
        out = capsys.readouterr().out
        assert real_root_parent in out
        assert "account-1" not in out

    def test_needle_absent_from_multi_root_refusal_error(self, tmp_path, monkeypatch, capsys, fake_config_dir_factory):
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        self._seed_needle_session(default_dir / "projects")
        monkeypatch.setattr(_mod, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")
        with pytest.raises(SystemExit) as exc_info:
            _mod.cmd_context_composition(
                _context_composition_args(no_redact=True, extra_config_dirs=[str(acct_b)])
            )
        assert exc_info.value.code == 2
        combined = "".join(capsys.readouterr())
        assert self._FAKE_PATH not in combined
        assert self._FAKE_SESSION_UUID not in combined

    def test_needle_absent_from_category_table_at_multi_root_default_redaction(
        self, tmp_path, monkeypatch, capsys, fake_config_dir_factory
    ):
        """Multi-root, default redaction (no --no-redact), needle content actually present and
        the scan not refused (so a category table is actually printed) -- the exact combination
        existing tests miss: existing multi-root tests use innocuous fixture text, and existing
        needle tests are single-root or the refusal path only."""
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        proj = default_dir / "projects" / "-home-user-testrepo"
        proj.mkdir(parents=True)
        records = [
            _user_msg(f"read {self._FAKE_PATH}", ts="2026-05-19T10:00:00.000Z"),
            _asst(
                "claude-sonnet-5",
                content=[{"type": "text", "text": f"session {self._FAKE_SESSION_UUID} looked at this"}],
                ts="2026-05-19T10:00:05.000Z",
            ),
        ]
        records[1]["message"]["usage"] = {"input_tokens": 100}  # keeps the residual non-negative
        _write_jsonl(proj / f"{self._FAKE_SESSION_UUID}.jsonl", records)
        monkeypatch.setattr(_mod, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")

        _mod.cmd_context_composition(_context_composition_args(extra_config_dirs=[str(acct_b)]))
        out = capsys.readouterr().out

        assert "## Category" in out  # confirms the scan wasn't refused -- the table actually printed
        assert self._FAKE_PATH not in out
        assert self._FAKE_SESSION_UUID not in out

    def test_no_per_account_or_per_project_composition_row_at_multi_root(
        self, tmp_path, monkeypatch, capsys, fake_config_dir_factory
    ):
        """No per-root/per-account/per-project composition breakdown at any root count --
        scoped to exclude the context-distribution-style per-root scan-summary diagnostic lines
        ('context-composition: account-N: scanned ...'), which carry counts only, never
        composition data, and which every multi-root sibling also emits."""
        default_dir = tmp_path / "default"
        (default_dir / "projects").mkdir(parents=True)
        proj_a = default_dir / "projects" / "-home-user-a"
        proj_a.mkdir(parents=True)
        proj_a_recs = [_asst("claude-sonnet-5", content=[{"type": "text", "text": "hello"}], ts="2026-05-19T10:00:00.000Z")]
        proj_a_recs[0]["message"]["usage"] = {"input_tokens": 1}
        _write_jsonl(proj_a / "sess.jsonl", proj_a_recs)
        monkeypatch.setattr(_mod, "config_dir", lambda: default_dir)
        acct_b = fake_config_dir_factory("acct-b")

        _mod.cmd_context_composition(_context_composition_args(extra_config_dirs=[str(acct_b)]))
        out = capsys.readouterr().out

        scan_summary_re = re.compile(r"^(WARNING: )?context-composition: account-\d+: (scanned|cannot scan|no transcripts found)")
        non_summary_lines = [ln for ln in out.splitlines() if not scan_summary_re.match(ln)]
        assert not any("account-" in ln for ln in non_summary_lines)


class TestDirectCallerDefenseInDepth:
    def test_direct_caller_with_no_redact_and_multiple_roots_refuses(self, tmp_path):
        """_resolve_cost_roots is the CLI-level enforcement point for the --no-redact +
        multi-root refusal, but _context_composition_report re-checks it itself for a direct
        caller that bypasses that boundary (this module's own tests included) -- proven here by
        calling it directly with roots=[...] rather than through cmd_context_composition."""
        args = _context_composition_args(no_redact=True)
        with pytest.raises(SystemExit) as exc_info:
            _mod._context_composition_report(args, roots=[tmp_path / "root-a", tmp_path / "root-b"])
        assert exc_info.value.code == 2


class TestConfigDirRouting:
    def test_context_composition_registered_in_subcommands_with_own_config_dir(self):
        assert "context-composition" in _mod._SUBCOMMANDS_WITH_OWN_CONFIG_DIR

    def test_top_level_config_dir_refused_end_to_end(self, monkeypatch, tmp_path, capsys):
        other_account = tmp_path / "other-account"
        (other_account / "projects").mkdir(parents=True)
        active_config_dir = tmp_path / "active-account"
        (active_config_dir / "projects").mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(active_config_dir))

        monkeypatch.setattr(
            sys, "argv",
            ["transcript-analysis.py", "--config-dir", str(other_account), "context-composition"],
        )
        with pytest.raises(SystemExit) as exc_info:
            _mod.main()

        assert exc_info.value.code == 2
        assert other_account / "projects" != _mod.PROJECTS_DIR
        err = capsys.readouterr().err
        assert "--config-dir" in err
        assert "context-composition" in err


class TestCategoryCoverage:
    def _mixed_shape_records(self) -> list[dict]:
        return [
            _user_msg([
                {"type": "text", "text": "fresh prompt"},
                {"type": "image", "source": {"type": "base64", "data": "not-real-image-bytes"}},
            ], ts="2026-05-19T10:00:00.000Z"),
            _asst(
                "claude-sonnet-5",
                content=[
                    {"type": "thinking", "thinking": "planning"},
                    _bash_use("t1", "echo hi"),
                    {"type": "text", "text": "assistant reply"},
                ],
                ts="2026-05-19T10:00:05.000Z",
            ),
            _user_msg([_tool_result("t1", "hi")], ts="2026-05-19T10:00:10.000Z"),
        ]

    def test_every_item_lands_in_exactly_one_category(self):
        records = self._mixed_shape_records()
        records[1]["message"]["usage"] = {"input_tokens": 1}
        stats = _mod._scan_context_composition_session([records], None)
        # 2 items in the first user record (text + image) + 3 in the assistant record
        # (thinking + tool_use + text) + 1 in the closing user record (tool_result) = 6.
        assert sum(stats["item_counts"].values()) == 6
        assert stats["unclassified_count"] == 1
        assert stats["item_counts"][_mod._CATEGORY_UNCLASSIFIED] == 1

    def test_unclassified_shape_increments_counter_only_never_content(self):
        """The unclassified image block's own payload ('not-real-image-bytes') never appears
        anywhere in the returned stats structure -- only its structural size and a count."""
        records = self._mixed_shape_records()
        records[1]["message"]["usage"] = {"input_tokens": 1}
        stats = _mod._scan_context_composition_session([records], None)
        assert "not-real-image-bytes" not in repr(stats)


class TestClassifyContentItem:
    """Direct unit coverage of _classify_content_item -- the scan-level tests above exercise it
    only indirectly through full sequences."""

    def test_compact_summary_text_classified_distinctly_from_a_fresh_prompt(self):
        category, size = _mod._classify_content_item("user", "carried forward digest", is_compact_summary=True)
        assert category == _mod._CATEGORY_COMPACT_SUMMARY
        assert size == len("carried forward digest") // _mod._READ_SCOPE_CHARS_PER_TOKEN

    def test_ordinary_user_text_classified_as_user_text(self):
        category, _size = _mod._classify_content_item("user", "hello", is_compact_summary=False)
        assert category == _mod._CATEGORY_USER_TEXT

    def test_thinking_block_classified_as_assistant_thinking(self):
        category, size = _mod._classify_content_item("assistant", {"type": "thinking", "thinking": "abcd"})
        assert category == _mod._CATEGORY_ASSISTANT_THINKING
        assert size == 1

    def test_tool_use_sized_from_its_input_payload(self):
        block = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "echo hi"}}
        category, size = _mod._classify_content_item("assistant", block)
        assert category == _mod._CATEGORY_TOOL_CALL
        assert size >= 0

    def test_mcp_tool_name_not_exposed_by_classify_itself(self):
        """_classify_content_item returns the unqualified _CATEGORY_TOOL_CALL constant -- the
        MCP-name collapse happens in the sequence scan's own tool_name_by_id bookkeeping, using
        _normalize_composition_tool_name (asserted separately below)."""
        block = _agent_use("t1", "staff-backend-engineer", tool_name="mcp__github__list_prs")
        category, _size = _mod._classify_content_item("assistant", block)
        assert category == _mod._CATEGORY_TOOL_CALL

    def test_normalize_composition_tool_name_collapses_mcp_names(self):
        assert _mod._normalize_composition_tool_name("mcp__github__list_prs") == _mod._MCP_TOOL_BUCKET_LABEL
        assert _mod._normalize_composition_tool_name("Bash") == "Bash"
        assert _mod._normalize_composition_tool_name(None) == "unknown"
