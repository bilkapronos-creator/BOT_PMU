"""Tests B3 — value_detectors, schedule_risk, pipeline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from velora_engine.analysis.schedule_risk import (
    build_schedule_index,
    check_upcoming_schedule_risk,
)
from velora_engine.analysis.value_detectors import detect_all_free_values
from velora_engine.analysis.pipeline import build_api_document_from_state
from velora_engine.models import MarketsRaw, OuLine


def _synthetic_state_meknes() -> dict:
    """Favori dom fort → value 1N2 uniquement."""
    return {
        "matches": {
            "100": {
                "sportId": 1,
                "matchId": 100,
                "competitor1Name": "CODM Meknès",
                "competitor2Name": "Olympique Dcheira",
                "matchStart": 1780341600,
                "status": "PREMATCH",
                "mainBetId": "b1",
            }
        },
        "bets": {
            "b1": {
                "matchId": 100,
                "betTypeName": "Résultat du match",
                "outcomes": ["o1", "oN", "o2"],
            }
        },
        "outcomes": {
            "o1": {"label": "1", "code": "1", "percentDistribution": 0.81},
            "oN": {"label": "N", "code": "N", "percentDistribution": 0.15},
            "o2": {"label": "2", "code": "2", "percentDistribution": 0.04},
        },
        "odds": {"o1": 1.96, "oN": 3.2, "o2": 4.1},
    }


def _synthetic_state_psg_rotation() -> dict:
    league_ts = 1780341600
    ucl_ts = league_ts + 4 * 86400
    return {
        "matches": {
            "200": {
                "sportId": 1,
                "matchId": 200,
                "competitor1Name": "PSG",
                "competitor2Name": "Amiens SC",
                "matchStart": league_ts,
                "status": "PREMATCH",
                "mainBetId": "b2",
                "categoryName": "Ligue 1",
            },
            "201": {
                "sportId": 1,
                "matchId": 201,
                "competitor1Name": "PSG",
                "competitor2Name": "Bayern Munich",
                "matchStart": ucl_ts,
                "status": "PREMATCH",
                "mainBetId": "b3",
                "categoryName": "Ligue des Champions",
            },
        },
        "bets": {
            "b2": {
                "matchId": 200,
                "betTypeName": "Résultat du match",
                "outcomes": ["o1", "oN", "o2"],
            },
            "b3": {
                "matchId": 201,
                "betTypeName": "Résultat du match",
                "outcomes": ["o1b", "oNb", "o2b"],
            },
        },
        "outcomes": {
            "o1": {"label": "1", "code": "1"},
            "oN": {"label": "N", "code": "N"},
            "o2": {"label": "2", "code": "2"},
            "o1b": {"label": "1", "code": "1"},
            "oNb": {"label": "N", "code": "N"},
            "o2b": {"label": "2", "code": "2"},
        },
        "odds": {"o1": 1.22, "oN": 6.5, "o2": 11.0, "o1b": 2.1, "oNb": 3.5, "o2b": 3.0},
    }


def test_meknes_badges_empty_when_only_1n2():
    markets = MarketsRaw(
        over_under_total={"2.5": OuLine(plus_cote=1.9, moins_cote=1.9, plus_prob=50)},
    )
    res = detect_all_free_values(
        cotes_1n2={"1": 1.96, "N": 3.2, "2": 4.1},
        probs={"1": 81, "N": 15, "2": 4},
        markets=markets,
        home="CODM Meknès",
        away="Olympique Dcheira",
    )
    assert res.primary_pick is not None
    assert res.primary_pick.market == "1n2"
    assert res.display_badges == []
    assert not any(v.market in ("ou_total", "btts") for v in res.value_bets) or all(
        v.market == "1n2" for v in res.value_bets if v.market not in ("ou_total", "btts", "team_goals_home", "team_goals_away")
    )


def test_ou_value_yields_badge():
    markets = MarketsRaw(
        over_under_total={
            "2.5": OuLine(plus_cote=1.45, moins_cote=2.65, plus_prob=75),
        },
    )
    res = detect_all_free_values(
        cotes_1n2={"1": 1.5, "N": 4.0, "2": 5.0},
        probs={"1": 40, "N": 30, "2": 30},
        markets=markets,
    )
    assert any(v.market == "ou_total" for v in res.value_bets)
    assert len(res.display_badges) >= 1


def test_schedule_rotation_alert():
    state = _synthetic_state_psg_rotation()
    idx = build_schedule_index(state)
    from velora_engine.scrape.markets_extractor import extract_competition_meta
    from velora_engine.scrape.winamax_state import find_raw_match

    raw = find_raw_match(state, 200)
    comp = extract_competition_meta(raw, state)
    alert = check_upcoming_schedule_risk(
        match_id="200",
        home="PSG",
        away="Amiens SC",
        match_start_ts=1780341600,
        cotes_1n2={"1": 1.22, "N": 6.5, "2": 11.0},
        current_competition=comp,
        schedule_index=idx,
    )
    assert alert is not None
    assert alert.type == "rotation_risk"


def test_pipeline_document_v2_shape():
    doc = build_api_document_from_state(_synthetic_state_meknes())
    assert doc.schema_version == 2
    assert len(doc.matchs) >= 1
    m = doc.matchs[0]
    assert m.free_analysis.primary_pick is not None
    assert "premium_analysis" in m.to_dict()
    d = m.to_dict()
    assert d["free_analysis"]["display_badges"] == []


def test_pipeline_rotation_in_pro_alerts():
    doc = build_api_document_from_state(_synthetic_state_psg_rotation())
    psg_match = next(
        (m for m in doc.matchs if m.equipe_domicile == "PSG" and "Amiens" in m.equipe_exterieur),
        None,
    )
    assert psg_match is not None
    assert any(a.type == "rotation_risk" for a in psg_match.pro_alerts)
    assert psg_match.confidence.adjusted_confidence is not None
    assert psg_match.confidence.adjusted_confidence < (psg_match.confidence.base_confidence or 1)


if __name__ == "__main__":
    tests = [
        test_meknes_badges_empty_when_only_1n2,
        test_ou_value_yields_badge,
        test_schedule_rotation_alert,
        test_pipeline_document_v2_shape,
        test_pipeline_rotation_in_pro_alerts,
    ]
    for fn in tests:
        fn()
        print(f"OK {fn.__name__}")
    print(f"All {len(tests)} B3 tests passed.")
