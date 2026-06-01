"""Tests B1 models + B2 markets_extractor."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from velora_engine.config import EDGE_THRESHOLDS, SCHEMA_VERSION
from velora_engine.models import build_example_document
from velora_engine.scrape.markets_extractor import (
    extract_all_markets,
    extract_competition_meta,
    extract_over_under_total,
)


def test_schema_version_fixture():
    doc = build_example_document()
    d = doc.to_dict()
    assert d["schema_version"] == SCHEMA_VERSION == 2
    assert len(d["matchs"]) == 2
    m0 = d["matchs"][0]
    assert "free_analysis" in m0 and "premium_analysis" in m0
    assert m0["free_analysis"]["display_badges"] == []
    assert m0["premium_analysis"]["score_exact"]["top3"]
    assert "_legacy" in m0


def test_edge_thresholds_premium_stricter():
    assert EDGE_THRESHOLDS["1n2"] == 1.05
    assert EDGE_THRESHOLDS["score_exact"] >= 1.15
    assert EDGE_THRESHOLDS["buteur_match"] >= 1.15


def test_extract_ou_dynamic_synthetic():
    outcomes = {
        "o1": {"label": "Plus de 1,5", "percentDistribution": 0.7},
        "o2": {"label": "Moins de 1,5", "percentDistribution": 0.3},
        "o3": {"label": "Plus de 3,5", "percentDistribution": 0.4},
        "o4": {"label": "Moins de 3,5", "percentDistribution": 0.6},
    }
    odds = {"o1": 1.3, "o2": 3.2, "o3": 2.9, "o4": 1.4}
    bets = [
        {
            "matchId": "1",
            "betTypeName": "Nombre de buts",
            "outcomes": ["o1", "o2", "o3", "o4"],
        }
    ]
    ou = extract_over_under_total(bets, outcomes, odds)
    assert "1.5" in ou and "3.5" in ou
    assert ou["1.5"].plus_cote == 1.3


def test_competition_friendly():
    meta = extract_competition_meta(
        {},
        None,
        fallback_title="Club Friendlies — Test",
    )
    assert meta.type == "friendly"
    assert meta.stakes_tier == "low"


def test_extract_all_markets_team_goals():
    home, away = "PSG", "Lyon"
    outcomes = {
        "h1": {"label": "Plus de 1,5"},
        "h2": {"label": "Moins de 1,5"},
    }
    odds = {"h1": 2.0, "h2": 1.7}
    bets = [
        {
            "matchId": "99",
            "betTypeName": "Nombre de buts - PSG",
            "outcomes": ["h1", "h2"],
        }
    ]
    ext = extract_all_markets(bets, outcomes, odds, home=home, away=away)
    assert "home" in ext.markets_raw.team_goals
    assert ext.markets_raw.team_goals["home"].team_name == home


def test_fixture_file_valid_json():
    path = ROOT / "velora_engine" / "fixtures" / "api_velora_matchs_v2.example.json"
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 2


if __name__ == "__main__":
    tests = [
        test_schema_version_fixture,
        test_edge_thresholds_premium_stricter,
        test_extract_ou_dynamic_synthetic,
        test_competition_friendly,
        test_extract_all_markets_team_goals,
        test_fixture_file_valid_json,
    ]
    for fn in tests:
        fn()
        print(f"OK {fn.__name__}")
    print(f"All {len(tests)} tests passed.")
