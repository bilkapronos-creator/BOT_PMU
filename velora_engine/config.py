"""Constantes Velora Engine v2 — seuils edge par marché."""

from __future__ import annotations

import json
from pathlib import Path

SCHEMA_VERSION = 2
ENGINE_ID = "velora-pro-2"

# edge = prob_modèle × cote ; value si edge >= seuil
EDGE_THRESHOLD_1N2 = 1.05
EDGE_THRESHOLD_OU_TOTAL = 1.06
EDGE_THRESHOLD_BTTS = 1.06
EDGE_THRESHOLD_TEAM_GOALS = 1.08
EDGE_THRESHOLD_SCORE_EXACT = 1.15
EDGE_THRESHOLD_BUTEUR_MATCH = 1.18
EDGE_THRESHOLD_BUTEUR_MI_TEMPS = 1.20
EDGE_THRESHOLD_BUTEUR_DOUBLE = 1.20

# Filtre outsiders 1N2 : pas de value extrême si favori < seuil et outsider >= cote min
OUTSIDER_FAVORI_COTE_MAX = 1.45
OUTSIDER_COTE_MIN = 5.0

# Conseils intelligents : cote < seuil = faible rendement sauf edge exceptionnel
MIN_COTE_AVANTAGEUSE = 1.50
LOW_ODDS_EDGE_MIN = 1.08
ADVISOR_TOP_N = 5

EDGE_THRESHOLDS: dict[str, float] = {
    "1n2": EDGE_THRESHOLD_1N2,
    "dc_1x": EDGE_THRESHOLD_1N2,
    "dc_x2": EDGE_THRESHOLD_1N2,
    "ou_total": EDGE_THRESHOLD_OU_TOTAL,
    "btts": EDGE_THRESHOLD_BTTS,
    "team_goals_home": EDGE_THRESHOLD_TEAM_GOALS,
    "team_goals_away": EDGE_THRESHOLD_TEAM_GOALS,
    "score_exact": EDGE_THRESHOLD_SCORE_EXACT,
    "buteur_match": EDGE_THRESHOLD_BUTEUR_MATCH,
    "buteur_mi_temps": EDGE_THRESHOLD_BUTEUR_MI_TEMPS,
    "buteur_double": EDGE_THRESHOLD_BUTEUR_DOUBLE,
    "dnb": EDGE_THRESHOLD_1N2,
    "half_time_1n2": EDGE_THRESHOLD_1N2,
    "handicap": EDGE_THRESHOLD_1N2,
    "exact_goals": EDGE_THRESHOLD_OU_TOTAL,
}

# Durée de transition front (nœud _legacy)
LEGACY_TTL_WEEKS = 2

# Lignes O/U fréquentes (découverte dynamique au-delà de cette liste)
DEFAULT_OU_LINES = ("0.5", "1.5", "2.5", "3.5", "4.5")

_CALIB_PATH = Path(__file__).resolve().parents[1] / "web" / "velora_foot_calibration.json"


def _apply_foot_calibration() -> None:
    global EDGE_THRESHOLDS  # noqa: PLW0603
    if not _CALIB_PATH.is_file():
        return
    try:
        data = json.loads(_CALIB_PATH.read_text(encoding="utf-8"))
        overrides = data.get("edge_thresholds") if isinstance(data, dict) else None
        if not isinstance(overrides, dict):
            return
        for key, val in overrides.items():
            if key in EDGE_THRESHOLDS and isinstance(val, (int, float)):
                EDGE_THRESHOLDS[key] = float(val)
    except (OSError, json.JSONDecodeError, TypeError):
        pass


_apply_foot_calibration()
