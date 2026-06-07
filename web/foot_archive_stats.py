"""
Stats archives Foot — regroupement par marché et suggestions de calibration edge.
"""

from __future__ import annotations

from typing import Any

# Seuils par défaut (alignés velora_engine/config.py)
DEFAULT_EDGE: dict[str, float] = {
    "1n2": 1.05,
    "dc_1x": 1.05,
    "dc_x2": 1.05,
    "ou_total": 1.06,
    "btts": 1.06,
    "team_goals_home": 1.08,
    "team_goals_away": 1.08,
    "score_exact": 1.15,
}

MARCHE_LABELS: dict[str, str] = {
    "pronostic_1n2": "Pronostic 1N2",
    "value_1n2": "Value 1N2",
    "over_25": "Over 2.5",
    "under_25": "Under 2.5",
    "btts": "BTTS",
    "score_exact": "Score exact",
    "double_chance": "Double chance",
    "autre": "Autre",
}

MARCHE_TO_EDGE_KEY: dict[str, str] = {
    "pronostic_1n2": "1n2",
    "value_1n2": "1n2",
    "over_25": "ou_total",
    "under_25": "ou_total",
    "btts": "btts",
    "score_exact": "score_exact",
    "double_chance": "dc_1x",
    "autre": "1n2",
}

MIN_SAMPLES_CALIB = 5
TARGET_WIN_RATE = 0.52
EDGE_STEP = 0.02
EDGE_MAX_BUMP = 0.08


def _texte_archive(archive: dict) -> str:
    parts = [
        str(archive.get("conseil") or ""),
        str(archive.get("opportunite_detail") or ""),
        str(archive.get("selection") or ""),
        str(archive.get("type_pari_foot") or ""),
    ]
    return " ".join(parts).lower()


def _est_value_bet(archive: dict) -> bool:
    blob = _texte_archive(archive)
    return "value bet" in blob or "value bet détecté" in blob or "value bet detecte" in blob


def classifier_marche_archive(archive: dict) -> tuple[str, str]:
    """Retourne (cle_marche, libelle_ui)."""
    marche = str(archive.get("marche") or archive.get("opportunite_type") or "").strip().lower()
    sel = str(archive.get("selection") or archive.get("type_pari_foot") or "").lower()
    blob = _texte_archive(archive)

    if marche in ("over_25", "ou_total") or "over 2.5" in sel or "+2.5" in blob:
        return "over_25", MARCHE_LABELS["over_25"]
    if marche == "under_25" or "under 2.5" in sel or "moins de 2.5" in blob:
        return "under_25", MARCHE_LABELS["under_25"]
    if marche == "btts" or "btts" in sel or "les 2 équipes" in blob or "les 2 equipes" in blob:
        return "btts", MARCHE_LABELS["btts"]
    if marche == "score_exact" or "score exact" in blob:
        return "score_exact", MARCHE_LABELS["score_exact"]
    if "double chance" in blob or marche.startswith("dc_"):
        return "double_chance", MARCHE_LABELS["double_chance"]
    if marche == "1n2" or "1n2" in sel or any(x in sel for x in ("domicile", "extérieur", "exterieur", "nul")):
        if _est_value_bet(archive):
            return "value_1n2", MARCHE_LABELS["value_1n2"]
        return "pronostic_1n2", MARCHE_LABELS["pronostic_1n2"]
    if _est_value_bet(archive):
        return "value_1n2", MARCHE_LABELS["value_1n2"]
    if "victoire" in blob:
        return "pronostic_1n2", MARCHE_LABELS["pronostic_1n2"]
    return "autre", MARCHE_LABELS["autre"]


def _archive_gagnee(archive: dict) -> bool:
    if archive.get("reussi_foot") is True:
        tp = str(archive.get("type_pari_foot") or "").strip().lower()
        return tp != "perdu"
    sp = str(archive.get("statut_pari") or "").upper().replace("É", "E")
    return sp in ("GAGNANT", "GAGNE")


def agreger_par_marche(archives: list[dict]) -> dict[str, dict[str, Any]]:
    """Stats par famille de marché (tous matchs terminés avec résultat)."""
    buckets: dict[str, dict[str, Any]] = {}
    for archive in archives:
        if not isinstance(archive, dict):
            continue
        if archive.get("reussi_foot") is None and not archive.get("statut_pari"):
            continue
        cle, label = classifier_marche_archive(archive)
        if cle not in buckets:
            buckets[cle] = {
                "label": label,
                "total": 0,
                "succes": 0,
                "taux": 0,
                "profit_net": 0.0,
            }
        buckets[cle]["total"] += 1
        if _archive_gagnee(archive):
            buckets[cle]["succes"] += 1
        fin = archive.get("financier") if isinstance(archive.get("financier"), dict) else {}
        try:
            buckets[cle]["profit_net"] += float(fin.get("profit") or archive.get("profit") or 0)
        except (TypeError, ValueError):
            pass
    for stats in buckets.values():
        total = stats["total"]
        stats["taux"] = round(stats["succes"] / total * 100) if total else 0
        stats["profit_net"] = round(float(stats["profit_net"]), 2)
    return buckets


def suggerer_calibration(
    par_marche: dict[str, dict[str, Any]],
    *,
    edge_defaults: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Ajuste les seuils edge si un marché sous-performe sur l'historique."""
    edge_defaults = edge_defaults or dict(DEFAULT_EDGE)
    edge_out = dict(edge_defaults)
    suggestions: list[dict[str, Any]] = []

    for cle, stats in par_marche.items():
        total = int(stats.get("total") or 0)
        if total < MIN_SAMPLES_CALIB:
            continue
        taux = int(stats.get("taux") or 0) / 100.0
        edge_key = MARCHE_TO_EDGE_KEY.get(cle, "1n2")
        current = float(edge_out.get(edge_key, edge_defaults.get(edge_key, 1.08)))
        if taux < TARGET_WIN_RATE - 0.08:
            bump = min(EDGE_MAX_BUMP, EDGE_STEP * max(1, int((TARGET_WIN_RATE - taux) / 0.1)))
            suggested = round(min(1.35, current + bump), 3)
            if suggested > current:
                edge_out[edge_key] = suggested
                suggestions.append(
                    {
                        "marche": cle,
                        "label": stats.get("label") or cle,
                        "edge_key": edge_key,
                        "edge_actuel": current,
                        "edge_suggere": suggested,
                        "taux_pct": int(stats.get("taux") or 0),
                        "echantillon": total,
                        "raison": f"{taux:.0%} de réussite sur {total} matchs — seuil relevé",
                    }
                )
    return {
        "edge_thresholds": edge_out,
        "suggestions": suggestions,
        "echantillon_min": MIN_SAMPLES_CALIB,
    }


def build_foot_stats_payload(archives: list[dict]) -> dict[str, Any]:
    par_marche = agreger_par_marche(archives)
    calibration = suggerer_calibration(par_marche)
    # Compat vitrine : detail_par_type_pari (libellés fins)
    detail_fine: dict[str, dict[str, Any]] = {}
    for archive in archives:
        if archive.get("reussi_foot") is None and not archive.get("statut_pari"):
            continue
        label = str(archive.get("type_pari_foot") or archive.get("selection") or "Foot")
        if label.strip().lower() == "perdu":
            label = classifier_marche_archive(archive)[1]
        if label not in detail_fine:
            detail_fine[label] = {"total": 0, "succes": 0, "taux": 0}
        detail_fine[label]["total"] += 1
        if _archive_gagnee(archive):
            detail_fine[label]["succes"] += 1
    for stats in detail_fine.values():
        stats["taux"] = (
            round(stats["succes"] / stats["total"] * 100) if stats["total"] else 0
        )
    return {
        "detail_par_marche": par_marche,
        "detail_par_type_pari": detail_fine,
        "calibration": calibration,
    }
