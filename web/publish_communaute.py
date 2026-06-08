"""Génère api_velora_communaute.json (PMU historique + finance + Foot)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from velora_finance import MISE_UNITAIRE, bloc_communaute_depuis_stats, fusionner_blocs_sports
from stats_foot import ecrire_calibration_foot, get_stats_foot_publiques
from stats_pmu import (
    extraire_historique_communaute_pmu,
    get_stats_publiques,
)

OUT = Path(__file__).resolve().parent / "api_velora_communaute.json"


def _extraire_historique_foot(archives: list, limit: int = 8) -> list[dict]:
    web_dir = Path(__file__).resolve().parent
    if str(web_dir) not in sys.path:
        sys.path.insert(0, str(web_dir))
    from velora_archiver_foot import (  # noqa: PLC0415
        _est_archive_terminee_validee,
        _est_en_attente,
    )

    candidats = [
        a
        for a in archives
        if (_est_archive_terminee_validee(a) or _est_en_attente(a))
        and (a.get("equipe_domicile") or a.get("equipe_exterieur"))
    ]
    candidats.sort(
        key=lambda a: a.get("match_start_ts") or a.get("timestamp") or 0,
        reverse=True,
    )
    out = []
    for a in candidats[:limit]:
        en_attente = _est_en_attente(a)
        type_pari = (
            a.get("type_pari_foot")
            or a.get("opportunite_detail")
            or a.get("conseil")
            or a.get("marche")
            or ""
        )
        out.append(
            {
                "equipe_domicile": a.get("equipe_domicile") or "?",
                "equipe_exterieur": a.get("equipe_exterieur") or "?",
                "score_final": "" if en_attente else (a.get("score_final") or ""),
                "type_pari_foot": type_pari if en_attente else (a.get("type_pari_foot") or type_pari),
                "reussi_foot": None if en_attente else a.get("reussi_foot"),
                "marche": a.get("marche") or a.get("opportunite_type") or "",
                "en_attente": en_attente,
                "match_start_ts": a.get("match_start_ts") or a.get("timestamp") or 0,
            }
        )
    return out


def construire_bloc_pmu() -> dict:
    """
    Bloc PMU complet : agrégat financier (archives) + détail Tiercé/types + historique courses.
    Le compteur « historique » (taux plateforme) est exposé pour fusion côté front avec Supabase.
    """
    pmu = get_stats_publiques()
    bloc = bloc_communaute_depuis_stats(
        {
            "taux": pmu.get("taux") or pmu.get("taux_reussite_plateforme", 0),
            "victoires": pmu.get("victoires") or pmu.get("victoires_plateforme", 0),
            "total": pmu.get("total") or pmu.get("courses_terminees_plateforme", 0),
            "mises_cumulees": pmu.get("mises_cumulees", 0),
            "profit_net": pmu.get("profit_net", 0),
            "roi_pct": pmu.get("roi_pct", 0),
        }
    )
    terminees = int(pmu.get("courses_terminees_plateforme") or 0)
    bloc["compteur_historique"] = {
        "courses_analysees": terminees,
        "courses_terminees": terminees,
        "victoires": int(pmu.get("victoires_plateforme") or 0),
        "taux_reussite": int(pmu.get("taux_reussite_plateforme") or 0),
    }
    bloc["gains_par_famille"] = pmu.get("gains_par_famille") or {}
    bloc["reussites_par_type"] = pmu.get("reussites_par_type") or {}
    bloc["historique_courses"] = extraire_historique_communaute_pmu()
    bloc["membres_actifs"] = int(pmu.get("membres_actifs") or 0)
    return bloc


def construire_bloc_foot() -> dict:
    foot = get_stats_foot_publiques()
    archives_path = Path(__file__).resolve().parent / "velora_archives_foot.json"
    archives: list = []
    if archives_path.is_file():
        try:
            raw = json.loads(archives_path.read_text(encoding="utf-8"))
            archives = raw if isinstance(raw, list) else []
        except (json.JSONDecodeError, OSError):
            archives = []
    bloc = bloc_communaute_depuis_stats(foot)
    bloc["detail_par_type_pari"] = foot.get("detail_par_type_pari") or {}
    bloc["detail_par_marche"] = foot.get("detail_par_marche") or {}
    bloc["calibration"] = foot.get("calibration") or {}
    bloc["historique_matchs"] = _extraire_historique_foot(archives)
    return bloc


def construire_communaute(extra_meta: dict | None = None) -> dict:
    """Fusion PMU + Foot sans écraser le détail historique PMU."""
    pmu_bloc = construire_bloc_pmu()
    foot_bloc = construire_bloc_foot()
    data = fusionner_blocs_sports(pmu_bloc, foot_bloc)
    meta = {
        "mise_unitaire": MISE_UNITAIRE,
        "genere_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    if extra_meta:
        meta.update(extra_meta)
    data["meta"] = meta
    return data


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    data = construire_communaute()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    ecrire_calibration_foot()
    pmu = data.get("pmu") or {}
    ch = pmu.get("compteur_historique") or {}
    print(
        f"[communaute] PMU {ch.get('victoires')}/{ch.get('courses_terminees')} "
        f"({ch.get('taux_reussite')}%) · Foot {data.get('foot', {}).get('total', 0)} matchs → {OUT.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
