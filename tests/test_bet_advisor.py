"""Tests conseils intelligents — probabilité × cote, filtre cotes basses."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from velora_engine.analysis.bet_advisor import build_intelligent_conseils
from velora_engine.models import MarketOutcome, MarketsRaw, OuLine


def test_favori_1_20_exclu_si_edge_faible():
    """Favori @ 1.20 avec 75% proba → edge 0.90, pas conseillé."""
    markets = MarketsRaw()
    conseils, best = build_intelligent_conseils(
        cotes_1n2={"1": 1.20, "N": 6.0, "2": 12.0},
        probs={"1": 75, "N": 15, "2": 10},
        markets=markets,
        home="France",
        away="Irlande",
    )
    labels = [c.label for c in conseils]
    assert not any("France" in l and "@ 1.20" in (c.label + str(c.cote)) for c in conseils for l in [c.label])
    assert all(c.cote is None or c.cote >= 1.50 or c.edge >= 1.08 for c in conseils)


def test_over_2_5_prefere_a_favori_bas():
    """Over @ 2.10 avec 58% doit passer avant un 1N2 faible rendement."""
    markets = MarketsRaw(
        over_under_total={
            "2.5": OuLine(plus_cote=2.10, moins_cote=1.75, plus_prob=58, moins_prob=42),
        },
    )
    conseils, best = build_intelligent_conseils(
        cotes_1n2={"1": 1.35, "N": 5.0, "2": 8.0},
        probs={"1": 72, "N": 18, "2": 10},
        markets=markets,
        prob_over_25_modele=58,
        home="PSG",
        away="Lens",
    )
    assert conseils
    assert best is not None
    ou = [c for c in conseils if c.market == "ou_total"]
    assert ou, "Over/Under doit figurer dans les conseils"
    assert not any(c.cote and c.cote < 1.35 and "PSG" in c.label for c in conseils)


def test_double_chance_scrapee():
    markets = MarketsRaw(
        double_chance={
            "x2": MarketOutcome(cote=2.40, prob=50),
        },
    )
    conseils, _ = build_intelligent_conseils(
        cotes_1n2={"1": 1.55, "N": 4.0, "2": 5.5},
        probs={"1": 58, "N": 24, "2": 18},
        markets=markets,
        home="A",
        away="B",
    )
    dc_labels = [c.label for c in conseils if "Double chance" in c.label]
    assert dc_labels


if __name__ == "__main__":
    test_favori_1_20_exclu_si_edge_faible()
    test_over_2_5_prefere_a_favori_bas()
    test_double_chance_scrapee()
    print("OK — test_bet_advisor")
