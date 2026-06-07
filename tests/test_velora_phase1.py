"""Phase 1 gratuite — modèle 1N2, snapshots cotes, filtre outsiders."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from velora_engine.analysis.model_1n2 import (
    blend_probabilities_1n2,
    confiance_niveau_from_context,
)
from velora_engine.analysis.value_detectors import detect_value_1n2
from velora_engine.odds_snapshots import (
    append_odds_snapshot,
    line_signal_for_pick,
)


def test_blend_probabilities_somme_100():
    cotes = {"1": 1.5, "N": 4.0, "2": 6.0}
    modele, marche = blend_probabilities_1n2(cotes, intel=None)
    assert sum(modele.values()) == 100
    assert sum(marche.values()) == 100
    assert all(modele[k] >= 0 for k in ("1", "N", "2"))


def test_confiance_faible_sans_intel():
    assert confiance_niveau_from_context(None, indice_velora=4) == "faible"


def test_line_signal_cote_baisse():
    hist = {"by_match": {"m1": [
        {"ts": 1, "cotes": {"1": 2.0, "N": 3.2, "2": 4.0}},
        {"ts": 2, "cotes": {"1": 1.85, "N": 3.2, "2": 4.0}},
    ]}}
    assert line_signal_for_pick(hist, "m1", "1") == "cote_baisse"


def test_outsider_value_filtre_favori_court():
  cotes = {"1": 1.11, "N": 9.0, "2": 15.0}
  probs = {"1": 85, "N": 8, "2": 7}
  hits = detect_value_1n2(cotes, probs, home="France", away="Irlande du Nord")
  picks = [vb.pick for vb in hits]
  assert "2" not in picks


def test_append_odds_snapshot_persist(tmp_path: Path):
    path = tmp_path / "hist.json"
    matchs = [{"id_match": "99", "free_analysis": {"cotes_1n2": {"1": 1.9, "N": 3.4, "2": 4.2}}}]
    append_odds_snapshot(path, matchs)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "99" in data["by_match"]
    assert len(data["by_match"]["99"]) == 1
