"""Statistiques Foot (archives validées) — même structure que stats_pmu."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from velora_finance import (
    MISE_UNITAIRE,
    agreger_stats_archives,
    est_archive_terminee_finance,
    est_victoire_archive,
)

ARCHIVES_FOOT_PATH = Path(__file__).resolve().parent / "velora_archives_foot.json"


def _charger_archives_foot() -> list[dict]:
    if not ARCHIVES_FOOT_PATH.is_file():
        return []
    try:
        data = json.loads(ARCHIVES_FOOT_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def get_stats_foot_publiques() -> dict[str, Any]:
    archives = _charger_archives_foot()
    agg = agreger_stats_archives(archives, champ_reussi="reussi_foot")
    par_type: dict[str, dict[str, Any]] = {}
    for archive in archives:
        if not est_archive_terminee_finance(archive, "reussi_foot"):
            continue
        label = archive.get("type_pari_foot") or archive.get("opportunite_type") or "Foot"
        if label == "Perdu":
            continue
        if label not in par_type:
            par_type[label] = {"total": 0, "succes": 0, "taux": 0}
        par_type[label]["total"] += 1
        if est_victoire_archive(archive, "reussi_foot"):
            par_type[label]["succes"] += 1
    for stats in par_type.values():
        stats["taux"] = (
            round(stats["succes"] / stats["total"] * 100) if stats["total"] else 0
        )
    return {
        "sport": "foot",
        "mise_unitaire": MISE_UNITAIRE,
        **agg,
        "matchs_termines": agg["total"],
        "detail_par_type_pari": par_type,
    }
