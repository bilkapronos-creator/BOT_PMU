"""Constantes Velora Engine v2 — seuils edge par marché."""

from __future__ import annotations

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
}

# Durée de transition front (nœud _legacy)
LEGACY_TTL_WEEKS = 2

# Lignes O/U fréquentes (découverte dynamique au-delà de cette liste)
DEFAULT_OU_LINES = ("0.5", "1.5", "2.5", "3.5", "4.5")
