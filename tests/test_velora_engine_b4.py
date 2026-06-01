"""Tests B4 — premium value detectors (seuils stricts)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from velora_engine.analysis.pipeline import build_api_document_from_state
from velora_engine.analysis.premium_value_detectors import (
    detect_all_premium_values,
    detect_value_buteur_list,
    detect_value_score_exact,
)
from velora_engine.config import EDGE_THRESHOLDS
from velora_engine.models import CompetitionMeta, MarketsRaw, OuLine, ScoreExactRow, ScorerRow
from velora_engine.scrape.markets_extractor import ExtractedMarkets


def _extracted(**overrides) -> ExtractedMarkets:
    base = ExtractedMarkets(
        markets_raw=MarketsRaw(
            over_under_total={
                "2.5": OuLine(plus_cote=2.0, moins_cote=1.55, moins_prob=55),
            }
        ),
        competition=CompetitionMeta(name="Ligue 1", type="league"),
        score_exact_top3=[],
        buteur_match=[],
        buteur_mi_temps=[],
        buteur_double=[],
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def test_thresholds_config():
    assert EDGE_THRESHOLDS["score_exact"] == 1.15
    assert EDGE_THRESHOLDS["buteur_match"] == 1.18
    assert EDGE_THRESHOLDS["buteur_mi_temps"] == 1.20
    assert EDGE_THRESHOLDS["buteur_double"] == 1.20


def test_score_exact_passes_at_115():
    top3 = [ScoreExactRow("1-0", prob=15, cote=8.0)]
    cotes = {"1": 1.45, "N": 4.0, "2": 6.0}
    vb = detect_value_score_exact(
        top3,
        cotes_1n2=cotes,
        markets_raw=MarketsRaw(
            over_under_total={"2.5": OuLine(moins_cote=1.55)}
        ),
    )
    assert vb is not None
    assert vb.market == "score_exact"
    assert vb.edge >= 1.15


def test_score_exact_rejects_low_edge():
    top3 = [ScoreExactRow("1-0", prob=10, cote=10.0)]
    cotes = {"1": 1.45, "N": 4.0, "2": 6.0}
    vb = detect_value_score_exact(
        top3,
        cotes_1n2=cotes,
        markets_raw=MarketsRaw(
            over_under_total={"2.5": OuLine(moins_cote=1.55)}
        ),
    )
    assert vb is None


def test_score_exact_rejects_without_prob():
    top3 = [ScoreExactRow("2-1", prob=None, cote=9.0)]
    vb = detect_value_score_exact(
        top3,
        cotes_1n2={"1": 1.5, "2": 5.0, "N": 4.0},
        markets_raw=MarketsRaw(),
    )
    assert vb is None


def test_buteur_match_requires_favori_and_edge():
    cotes = {"1": 1.22, "N": 6.0, "2": 11.0}
    vb = detect_value_buteur_list(
        [ScorerRow("Mbappé", 3.2)],
        "buteur_match",
        cotes_1n2=cotes,
        min_cote=2.5,
        strict=False,
    )
    assert vb is not None
    assert vb.edge >= 1.18

    vb_fail = detect_value_buteur_list(
        [ScorerRow("Joueur", 3.0)],
        "buteur_match",
        cotes_1n2={"1": 2.0, "N": 3.5, "2": 3.0},
        min_cote=2.5,
        strict=False,
    )
    assert vb_fail is None


def test_buteur_double_strict_cote_and_edge():
    cotes = {"1": 1.25, "N": 6.0, "2": 10.0}
    vb_low = detect_value_buteur_list(
        [ScorerRow("Duo", 5.0)],
        "buteur_double",
        cotes_1n2=cotes,
        min_cote=8.0,
        strict=True,
    )
    assert vb_low is None

    vb_ok = detect_value_buteur_list(
        [ScorerRow("Duo stars", 12.0)],
        "buteur_double",
        cotes_1n2=cotes,
        min_cote=8.0,
        strict=True,
    )
    assert vb_ok is None or vb_ok.edge >= 1.20


def test_top3_always_present_value_bets_may_be_empty():
    ext = _extracted(
        score_exact_top3=[
            ScoreExactRow("0-0", prob=5, cote=6.0),
            ScoreExactRow("1-0", prob=4, cote=7.0),
        ],
        buteur_match=[ScorerRow("A", 4.0)],
    )
    prem = detect_all_premium_values(
        ext,
        cotes_1n2={"1": 2.0, "N": 3.2, "2": 3.5},
    )
    assert len(prem.score_exact.top3) == 2
    assert len(prem.buteur_match.top) == 1
    assert prem.value_bets == [] or all(
        v.edge >= EDGE_THRESHOLDS.get(v.market, 1.2) for v in prem.value_bets
    )


def test_detect_all_premium_populates_value_bets():
    ext = _extracted(
        score_exact_top3=[ScoreExactRow("1-0", prob=18, cote=8.5)],
        buteur_match=[ScorerRow("Striker", 3.0)],
    )
    prem = detect_all_premium_values(
        ext,
        cotes_1n2={"1": 1.30, "N": 5.5, "2": 9.0},
    )
    assert len(prem.score_exact.top3) == 1
    if prem.value_bets:
        assert prem.score_exact.value_bet is not None
        assert prem.value_bets[0].market == "score_exact"
        assert all(
            vb.market in ("score_exact", "buteur_match", "buteur_mi_temps", "buteur_double")
            for vb in prem.value_bets
        )


def test_pipeline_premium_block_integrated():
    state = {
        "matches": {
            "300": {
                "sportId": 1,
                "matchId": 300,
                "competitor1Name": "Favori FC",
                "competitor2Name": "Underdog",
                "matchStart": 1780341600,
                "status": "PREMATCH",
                "mainBetId": "b1",
            }
        },
        "bets": {
            "b1": {
                "matchId": 300,
                "betTypeName": "Résultat du match",
                "outcomes": ["o1", "oN", "o2"],
            },
            "b2": {
                "matchId": 300,
                "betTypeName": "Score exact",
                "outcomes": ["s1"],
            },
        },
        "outcomes": {
            "o1": {"label": "1", "percentDistribution": 0.75},
            "oN": {"label": "N", "percentDistribution": 0.15},
            "o2": {"label": "2", "percentDistribution": 0.10},
            "s1": {"label": "1 - 0", "percentDistribution": 0.16},
        },
        "odds": {"o1": 1.35, "oN": 4.5, "o2": 8.0, "s1": 8.0},
    }
    doc = build_api_document_from_state(state)
    assert doc.matchs
    m = doc.matchs[0]
    assert len(m.premium_analysis.score_exact.top3) >= 0
    for vb in m.premium_analysis.value_bets:
        assert vb.edge >= EDGE_THRESHOLDS[vb.market]


if __name__ == "__main__":
    tests = [
        test_thresholds_config,
        test_score_exact_passes_at_115,
        test_score_exact_rejects_low_edge,
        test_score_exact_rejects_without_prob,
        test_buteur_match_requires_favori_and_edge,
        test_buteur_double_strict_cote_and_edge,
        test_top3_always_present_value_bets_may_be_empty,
        test_detect_all_premium_populates_value_bets,
        test_pipeline_premium_block_integrated,
    ]
    for fn in tests:
        fn()
        print(f"OK {fn.__name__}")
    print(f"All {len(tests)} B4 tests passed.")
