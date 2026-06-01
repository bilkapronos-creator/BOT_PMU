"""
Structure financière Velora (PMU & Foot) — mise unitaire, profit, ROI.
Même logique pour tous les sports : pari simple à mise fixe.
"""

from __future__ import annotations

from typing import Any

MISE_UNITAIRE = float(__import__("os").environ.get("VELORA_MISE_UNITAIRE", "10"))


def calculer_resultat_financier(
    gagne: bool, cote: float | None, mise: float | None = None
) -> dict[str, float]:
    """Profit net et ROI du pari (mise unique). Perte = -mise ; gain = mise * (cote - 1)."""
    m = float(mise if mise is not None else MISE_UNITAIRE)
    if not gagne:
        return {
            "mise": round(m, 2),
            "gain_brut": 0.0,
            "profit": round(-m, 2),
            "roi_pari": -100.0,
        }
    try:
        c = float(cote) if cote is not None else 0.0
    except (TypeError, ValueError):
        c = 0.0
    if c < 1.01:
        c = 1.01
    gain_brut = round(m * c, 2)
    profit = round(gain_brut - m, 2)
    roi_pari = round((profit / m) * 100, 1) if m > 0 else 0.0
    return {
        "mise": round(m, 2),
        "gain_brut": gain_brut,
        "profit": profit,
        "roi_pari": roi_pari,
    }


def _financier_archive(archive: dict) -> dict | None:
    fin = archive.get("financier")
    return fin if isinstance(fin, dict) else None


def est_archive_terminee_finance(archive: dict, champ_reussi: str) -> bool:
    statut = str(archive.get("statut") or "").strip().upper().replace(" ", "_")
    if statut in ("EN_ATTENTE", "EN ATTENTE", "EN_ATTENTE_DU_RESULTAT"):
        return False
    if archive.get("statut") == "En attente":
        return False
    val = archive.get(champ_reussi)
    return val is not None


def _cote_pmu_archive(archive: dict) -> float | None:
    """Cote du favori Velora (pronosticNumero) ou du premier du top."""
    for cheval in archive.get("pronostic_velora") or archive.get("top3") or []:
        if not isinstance(cheval, dict):
            continue
        fav = archive.get("pronosticNumero")
        if fav is not None and str(cheval.get("numero")) == str(fav):
            try:
                return float(cheval.get("cote"))
            except (TypeError, ValueError):
                pass
            break
    top = (archive.get("pronostic_velora") or archive.get("top3") or [None])[0]
    if isinstance(top, dict):
        try:
            return float(top.get("cote"))
        except (TypeError, ValueError):
            pass
    return None


def est_victoire_archive(archive: dict, champ_reussi: str) -> bool:
    if not est_archive_terminee_finance(archive, champ_reussi):
        return False
    if archive.get(champ_reussi) is not True:
        return False
    type_pari = archive.get("type_pari_foot") or archive.get("type_pari_pmu") or ""
    return str(type_pari).strip().lower() != "perdu"


def agreger_stats_archives(
    archives: list[dict],
    champ_reussi: str = "reussi_pmu",
) -> dict[str, Any]:
    """
    Agrégat communauté : taux, victoires, mises cumulées, profit net, ROI global.
    Structure identique PMU / Foot.
    """
    terminees = [a for a in archives if est_archive_terminee_finance(a, champ_reussi)]
    victoires = [a for a in terminees if est_victoire_archive(a, champ_reussi)]

    total = len(terminees)
    nb_vict = len(victoires)
    taux = round(nb_vict / total * 100) if total else 0

    mises_cumulees = 0.0
    profit_net = 0.0
    for a in terminees:
        fin = _financier_archive(a)
        if fin:
            mises_cumulees += float(fin.get("mise") or MISE_UNITAIRE)
            profit_net += float(fin.get("profit") or 0)
        else:
            mises_cumulees += MISE_UNITAIRE
            gagne = est_victoire_archive(a, champ_reussi)
            fin_calc = calculer_resultat_financier(gagne, _cote_pmu_archive(a))
            profit_net += float(fin_calc.get("profit") or 0)

    roi_pct = round((profit_net / mises_cumulees) * 100, 1) if mises_cumulees > 0 else 0.0

    return {
        "taux": taux,
        "victoires": nb_vict,
        "total": total,
        "mises_cumulees": round(mises_cumulees, 2),
        "profit_net": round(profit_net, 2),
        "roi_pct": roi_pct,
    }


def enrichir_archive_pmu_financier(archive: dict, evaluation: dict | None = None) -> dict:
    """Ajoute ou recalcule le bloc financier PMU (cote favori si disponible)."""
    evaluation = evaluation or {}
    gagne = evaluation.get("reussi_pmu")
    if gagne is None:
        gagne = archive.get("reussi_pmu") is True
    archive["financier"] = calculer_resultat_financier(gagne, _cote_pmu_archive(archive))
    archive["profit"] = archive["financier"].get("profit")
    return archive


def recalculer_financier_archives_pmu(archives: list[dict]) -> tuple[int, int]:
    """
    Recalcule financier + profit sur les archives PMU déjà évaluées.
    Retourne (nb_mises_a_jour, nb_ignorees).
    """
    maj = 0
    ignorees = 0
    for arch in archives:
        if arch.get("reussi_pmu") is None:
            ignorees += 1
            continue
        if str(arch.get("statut") or "") == "En attente":
            ignorees += 1
            continue
        enrichir_archive_pmu_financier(arch)
        maj += 1
    return maj, ignorees


def bloc_communaute_depuis_stats(stats: dict[str, Any]) -> dict[str, Any]:
    """Normalise un bloc sport pour api_velora_communaute.json."""
    return {
        "taux": int(stats.get("taux") or 0),
        "victoires": int(stats.get("victoires") or 0),
        "total": int(stats.get("total") or 0),
        "mises_cumulees": float(stats.get("mises_cumulees") or 0),
        "profit_net": float(stats.get("profit_net") or 0),
        "roi_pct": float(stats.get("roi_pct") or 0),
    }


def fusionner_blocs_sports(pmu: dict, foot: dict) -> dict[str, Any]:
    """Score global pondéré (mises + victoires)."""
    vict = int(pmu.get("victoires") or 0) + int(foot.get("victoires") or 0)
    tot = int(pmu.get("total") or 0) + int(foot.get("total") or 0)
    mises = float(pmu.get("mises_cumulees") or 0) + float(foot.get("mises_cumulees") or 0)
    profit = float(pmu.get("profit_net") or 0) + float(foot.get("profit_net") or 0)
    global_bloc = {
        "taux": round(vict / tot * 100) if tot else 0,
        "victoires": vict,
        "total": tot,
        "mises_cumulees": round(mises, 2),
        "profit_net": round(profit, 2),
        "roi_pct": round((profit / mises) * 100, 1) if mises > 0 else 0.0,
    }
    return {
        "pmu": bloc_communaute_depuis_stats(pmu),
        "foot": bloc_communaute_depuis_stats(foot),
        "global": bloc_communaute_depuis_stats(global_bloc),
    }
