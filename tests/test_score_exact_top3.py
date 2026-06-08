"""Validation score exact : victoire si le résultat est dans le top 3 proposé."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

from velora_archiver_foot import (  # noqa: E402
    _evaluer_score_exact_top3,
    _liste_scores_exact_proposes,
    valider_foot,
)


def test_liste_top3_dedup():
    match = {
        "score_exact": [
            {"score": "3 - 0", "cote": 7.0, "prob": 12},
            {"score": "2 - 0", "cote": 6.0, "prob": 10},
            {"score": "1 - 0", "cote": 7.5, "prob": 6},
            {"score": "3-0", "cote": 7.0, "prob": 5},
        ],
        "marche": "score_exact",
    }
    props = _liste_scores_exact_proposes(match)
    assert len(props) == 3
    assert props[0]["score"] == "3-0"
    assert props[1]["score"] == "2-0"


def test_victoire_si_dans_top3_pas_le_premier():
    match = {
        "score_exact": [
            {"score": "3 - 0", "cote": 7.0, "prob": 12},
            {"score": "2 - 0", "cote": 6.0, "prob": 10},
            {"score": "1 - 0", "cote": 7.5, "prob": 6},
        ],
        "marche": "score_exact",
        "opportunite_type": "score_exact",
        "id_match": "1",
        "equipe_domicile": "A",
        "equipe_exterieur": "B",
    }
    gagne, label, cote, labels = _evaluer_score_exact_top3(match, 2, 0)
    assert gagne is True
    assert label == "2-0"
    assert cote == 6.0
    assert len(labels) == 3

    archive = valider_foot(match, "2-0")
    assert archive is not None
    assert archive["reussi_foot"] is True
    assert archive["type_pari_foot"] == "Score exact 2-0"
    assert archive["validation_mode"] == "top3_ui"


def test_italie_0_1_dans_modele_gagnant():
    """Grèce–Italie : l'UI affiche 0-1 via top_scores_modele (pas les cotes bookmaker)."""
    match = {
        "id_match": "71422646",
        "equipe_domicile": "Grèce",
        "equipe_exterieur": "Italie",
        "marche": "score_exact",
        "opportunite_type": "score_exact",
        "free_analysis": {
            "pronostic_1n2": "2",
            "top_scores_modele": [
                {"score": "1-2", "prob": 7},
                {"score": "0-1", "prob": 4},
                {"score": "1-3", "prob": 4},
            ],
        },
        "score_exact": [
            {"score": "1 - 1", "cote": 5.8, "prob": 13},
            {"score": "1 - 0", "cote": 6.75, "prob": 2},
            {"score": "2 - 1", "cote": 7.75, "prob": 2},
        ],
    }
    archive = valider_foot(match, "0-1")
    assert archive is not None
    assert archive["reussi_foot"] is True
    assert archive["type_pari_foot"] == "Score exact 0-1"


def test_danemark_2_1_hors_modele_perdant():
    """Danemark–Ukraine : 2-1 absent du top 3 modèle affiché."""
    match = {
        "id_match": "71421980",
        "marche": "score_exact",
        "opportunite_type": "score_exact",
        "free_analysis": {
            "pronostic_1n2": "2",
            "top_scores_modele": [
                {"score": "1-2", "prob": 6},
                {"score": "0-1", "prob": 4},
                {"score": "2-3", "prob": 3},
            ],
        },
        "score_exact": [
            {"score": "1 - 0", "cote": 5.6, "prob": 30},
            {"score": "2 - 1", "cote": 7.5, "prob": 16},
        ],
    }
    archive = valider_foot(match, "2-1")
    assert archive is not None
    assert archive["reussi_foot"] is False


def test_defaite_hors_top3():
    match = {
        "score_exact": [
            {"score": "3 - 0", "cote": 7.0, "prob": 12},
            {"score": "2 - 0", "cote": 6.0, "prob": 10},
            {"score": "1 - 0", "cote": 7.5, "prob": 6},
        ],
        "marche": "score_exact",
        "opportunite_type": "score_exact",
        "id_match": "2",
    }
    archive = valider_foot(match, "1-1")
    assert archive is not None
    assert archive["reussi_foot"] is False
    assert archive["type_pari_foot"] == "Perdu"


if __name__ == "__main__":
    test_liste_top3_dedup()
    test_victoire_si_dans_top3_pas_le_premier()
    test_defaite_hors_top3()
    test_italie_0_1_dans_modele_gagnant()
    test_danemark_2_1_hors_modele_perdant()
    print("OK — test_score_exact_top3")
