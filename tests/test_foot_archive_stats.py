"""Tests stats archives Foot."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

from foot_archive_stats import (  # noqa: E402
    agreger_par_marche,
    classifier_marche_archive,
    suggerer_calibration,
)


def test_classif_pronostic_vs_value():
    pron = {"marche": "1n2", "conseil": "Victoire Autriche — forme favorable", "selection": "1N2 Domicile"}
    value = {"marche": "1n2", "conseil": "🔥 Value Bet Détecté : Nul", "selection": "1N2 Nul"}
    assert classifier_marche_archive(pron)[0] == "pronostic_1n2"
    assert classifier_marche_archive(value)[0] == "value_1n2"


def test_classif_over_25():
    a = {"marche": "over_25", "conseil": "🔥 Value Bet Détecté : Ext +2.5", "selection": "Over 2.5"}
    assert classifier_marche_archive(a)[0] == "over_25"


def test_agregation_taux():
    archives = [
        {"marche": "1n2", "conseil": "Victoire X", "reussi_foot": True, "statut_pari": "GAGNANT", "financier": {"profit": 4}},
        {"marche": "1n2", "conseil": "Victoire Y", "reussi_foot": False, "statut_pari": "PERDANT", "financier": {"profit": -10}},
        {"marche": "over_25", "conseil": "Value +2.5", "reussi_foot": False, "statut_pari": "PERDANT"},
    ]
    par = agreger_par_marche(archives)
    assert par["pronostic_1n2"]["total"] == 2
    assert par["pronostic_1n2"]["succes"] == 1
    assert par["over_25"]["total"] == 1


def test_score_exact_top3_agrege_victoires_et_perdus():
    from foot_archive_stats import build_foot_stats_payload

    archives = [
        {
            "marche": "score_exact",
            "type_pari_foot": "Score exact 0-1",
            "reussi_foot": True,
            "statut_pari": "GAGNANT",
        },
        {
            "marche": "score_exact",
            "type_pari_foot": "Perdu",
            "reussi_foot": False,
            "statut_pari": "PERDANT",
        },
        {
            "marche": "score_exact",
            "type_pari_foot": "Score exact 2-0",
            "reussi_foot": True,
            "statut_pari": "GAGNANT",
        },
    ]
    payload = build_foot_stats_payload(archives)
    coarse = payload["detail_par_marche"]["score_exact"]
    assert coarse["total"] == 3
    assert coarse["succes"] == 2
    assert coarse["taux"] == 67
    fine = payload["detail_par_type_pari"]["Score exact (top 3)"]
    assert fine["total"] == 3
    assert fine["succes"] == 2
    assert "Score exact 0-1" not in payload["detail_par_type_pari"]


def test_calibration_monte_seuil_si_mauvais():
    par = {
        "over_25": {"label": "Over 2.5", "total": 8, "succes": 2, "taux": 25},
    }
    cal = suggerer_calibration(par)
    assert cal["edge_thresholds"]["ou_total"] > 1.06
    assert cal["suggestions"]
