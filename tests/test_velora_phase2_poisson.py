"""Phase 2 — modèle Poisson maison."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from velora_engine.analysis.model_poisson import (
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
