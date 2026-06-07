"""Phase 2 — modèle Poisson maison."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from velora_engine.analysis.model_poisson import (
    align_top_scores_for_pick,
    attack_defense_from_form,
    build_poisson_analysis,
    probabilities_1n2_from_matrix,
    prob_over_25_from_matrix,
    score_probability_matrix,
    top_scores_from_matrix,
)
from velora_engine.models import MarketsRaw, OuLine


def test_matrix_somme_proche_un():
    matrix = score_probability_matrix(1.5, 1.1)
    total = sum(p for row in matrix for p in row)
    assert 0.99 <= total <= 1.01


def test_favori_dom_plus_probable():
    matrix = score_probability_matrix(2.2, 0.7)
    probs = probabilities_1n2_from_matrix(matrix)
    assert probs["1"] > probs["2"]


def test_over_25_eleve_match_offensif():
    matrix = score_probability_matrix(2.0, 1.8)
    assert prob_over_25_from_matrix(matrix) >= 55


def test_top_scores_format():
    matrix = score_probability_matrix(1.4, 1.2)
    rows = top_scores_from_matrix(matrix, limit=3)
    assert len(rows) >= 2
    assert "score" in rows[0] and "prob" in rows[0]


def test_france_favori_fort():
    intel = {
        "has_form": True,
        "home_form": {"played": 5, "wins": 4, "draws": 0, "losses": 1},
        "away_form": {"played": 5, "wins": 2, "draws": 1, "losses": 2},
    }
    cotes = {"1": 1.11, "N": 8.0, "2": 15.0}
    poisson = build_poisson_analysis(cotes=cotes, intel=intel, markets=MarketsRaw())
    assert poisson.probabilites_1n2["1"] >= 60
    assert poisson.lambda_home > poisson.lambda_away


def test_attack_defense_form():
    gf, ga = attack_defense_from_form({"played": 5, "wins": 5, "draws": 0, "losses": 0})
    gf2, ga2 = attack_defense_from_form({"played": 5, "wins": 0, "draws": 0, "losses": 5})
    assert gf > gf2
    assert ga2 > ga


def test_pipeline_top_scores_alignes_sur_pronostic():
    from velora_engine.analysis.pipeline import build_match_v2
    from velora_engine.analysis.schedule_risk import build_schedule_index

    state = {
        "bets": {
            "b1": {
                "matchId": "99",
                "betTypeName": "Résultat du match",
                "outcomes": ["o1", "oN", "o2"],
            }
        },
        "outcomes": {
            "o1": {"label": "Maroc", "percentDistribution": 0.31},
            "oN": {"label": "Nul", "percentDistribution": 0.27},
            "o2": {"label": "Norvège", "percentDistribution": 0.42},
        },
        "odds": {"o1": 3.0, "oN": 3.45, "o2": 2.15},
        "matches": {
            "99": {
                "matchId": "99",
                "sportId": 1,
                "mainBetId": "b1",
                "matchStart": "2026-06-07T19:00:00Z",
                "competitor1Name": "Maroc",
                "competitor2Name": "Norvège",
            }
        },
    }
    raw = state["matches"]["99"]
    built = build_match_v2(
        state=state,
        match_id="99",
        raw_match=raw,
        home="Maroc",
        away="Norvège",
        schedule_index=build_schedule_index(state),
    )
    assert built is not None
    pick = built.free_analysis.pronostic_1n2
    scores = built.free_analysis.top_scores_modele or []
    assert scores
    for row in scores:
        h, a = map(int, str(row["score"]).replace(" ", "").split("-"))
        if pick == "1":
            assert h > a
        elif pick == "2":
            assert h < a
        elif pick == "N":
            assert h == a


def test_top_scores_filtre_pronostic_ext():
    matrix = score_probability_matrix(0.75, 2.1)
    brut = top_scores_from_matrix(matrix, limit=8)
    assert brut
    assert any("-" in s["score"] for s in brut)
    ext = top_scores_from_matrix(matrix, limit=5, pick="2")
    assert len(ext) >= 1
    for row in ext:
        h, a = map(int, row["score"].split("-"))
        assert h < a
    alignes = align_top_scores_for_pick(brut, "2", matrix=matrix, limit=5)
    assert alignes
    for row in alignes:
        h, a = map(int, row["score"].split("-"))
        assert h < a
