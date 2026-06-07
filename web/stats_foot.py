"""Statistiques Foot (archives validées) — même structure que stats_pmu."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from foot_archive_stats import build_foot_stats_payload
from velora_finance import (
    MISE_UNITAIRE,
    agreger_stats_archives,
    est_archive_terminee_finance,
    est_victoire_archive,
)

ARCHIVES_FOOT_PATH = Path(__file__).resolve().parent / "velora_archives_foot.json"
_WEB_DIR = Path(__file__).resolve().parent


def _archives_foot_valides(archives: list[dict]) -> list[dict]:
    if str(_WEB_DIR) not in sys.path:
        sys.path.insert(0, str(_WEB_DIR))
    from velora_archiver_foot import _est_archive_terminee_validee, _match_deja_joue  # noqa: PLC0415

    return [
        a
        for a in archives
        if isinstance(a, dict) and _est_archive_terminee_validee(a) and _match_deja_joue(a)
    ]


def _charger_archives_foot() -> list[dict]:
    if not ARCHIVES_FOOT_PATH.is_file():
        return []
    try:
        data = json.loads(ARCHIVES_FOOT_PATH.read_text(encoding="utf-8"))
        raw = data if isinstance(data, list) else []
        return _archives_foot_valides(raw)
    except (json.JSONDecodeError, OSError):
        return []


def get_stats_foot_publiques() -> dict[str, Any]:
    archives = _charger_archives_foot()
    agg = agreger_stats_archives(archives, champ_reussi="reussi_foot")
    payload = build_foot_stats_payload(archives)
    taux = int(agg.get("taux") or 0)
    if not taux and agg.get("total"):
        victoires = int(agg.get("victoires") or 0)
        total = int(agg.get("total") or 0)
        taux = round(victoires / total * 100) if total else 0
    return {
        "sport": "foot",
        "mise_unitaire": MISE_UNITAIRE,
        **agg,
        "taux_reussite_plateforme": taux,
        "matchs_termines": agg["total"],
        "detail_par_type_pari": payload["detail_par_type_pari"],
        "detail_par_marche": payload["detail_par_marche"],
        "calibration": payload["calibration"],
    }


def ecrire_calibration_foot(path: Path | None = None) -> dict[str, Any]:
    """Écrit web/velora_foot_calibration.json pour le moteur value bets."""
    stats = get_stats_foot_publiques()
    cal = stats.get("calibration") or {}
    out = path or (Path(__file__).resolve().parent / "velora_foot_calibration.json")
    doc = {
        "version": 1,
        "edge_thresholds": cal.get("edge_thresholds") or {},
        "suggestions": cal.get("suggestions") or [],
        "detail_par_marche": stats.get("detail_par_marche") or {},
    }
    out.write_text(
        __import__("json").dumps(doc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return doc
