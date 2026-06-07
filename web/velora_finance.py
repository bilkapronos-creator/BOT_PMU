"""
Structure financière Velora (PMU & Foot) — mise unitaire, profit, ROI.
Même logique pour tous les sports : pari simple à mise fixe.
"""

from __future__ import annotations

from typing import Any

MISE_UNITAIRE = float(__import__("os").environ.get("VELORA_MISE_UNITAIRE", "10"))
# Cote de repli si PMU n'a pas renvoyé de rapport (affichage ROI / bénéfice)
COTE_PMU_DEFAUT_GAGNANT = float(
    __import__("os").environ.get("VELORA_COTE_PMU_DEFAUT", "2.5")
)


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


def _parse_cote(val) -> float | None:
    if val is None or val in ("-", "—", ""):
        return None
    try:
        from pmu_rapports_definitifs import parse_rapport_pmu_valeur

        return parse_rapport_pmu_valeur(val)
    except Exception:
        try:
            c = float(str(val).replace(",", "."))
            return c if c >= 1.01 else None
        except (TypeError, ValueError):
            return None


def _cote_pmu_archive(archive: dict) -> float | None:
    """Cote du favori Velora (pronosticNumero) ou du premier du top."""
    for cheval in archive.get("pronostic_velora") or archive.get("top3") or []:
        if not isinstance(cheval, dict):
            continue
        fav = archive.get("pronosticNumero")
        if fav is not None and str(cheval.get("numero")) == str(fav):
            c = _parse_cote(cheval.get("cote"))
            if c is not None:
                return c
            break
    for cheval in archive.get("pronostic_velora") or archive.get("top3") or []:
        if isinstance(cheval, dict):
            c = _parse_cote(cheval.get("cote"))
            if c is not None:
                return c
    return None


def _cote_pmu_effective(archive: dict) -> float:
    """
    Cote utilisée pour le financier : rapport définitif PMU, cote archivée, repli algo.
    """
    for cle in ("cote_jouee", "rapport_pmu"):
        c = _parse_cote(archive.get(cle))
        if c is not None:
            return c
    fin = archive.get("financier")
    if isinstance(fin, dict):
        c = _parse_cote(fin.get("cote"))
        if c is not None:
            return c
    cote = _cote_pmu_archive(archive)
    if cote is not None:
        return cote
    return COTE_PMU_DEFAUT_GAGNANT if archive.get("reussi_pmu") is True else 1.01


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
        gagne = est_victoire_archive(a, champ_reussi)
        if fin:
            mises_cumulees += float(fin.get("mise") or MISE_UNITAIRE)
            profit_val = float(fin.get("profit") or 0)
            if gagne and profit_val <= 0:
                profit_val = float(
                    calculer_resultat_financier(True, _cote_pmu_effective(a)).get("profit") or 0
                )
            profit_net += profit_val
        else:
            mises_cumulees += MISE_UNITAIRE
            fin_calc = calculer_resultat_financier(gagne, _cote_pmu_effective(a))
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
    cote = _cote_pmu_effective(archive)
    archive["financier"] = calculer_resultat_financier(gagne, cote)
    archive["financier"]["cote"] = cote if gagne else None
    archive["profit"] = archive["financier"].get("profit")
    return archive


def recalculer_financier_archives_pmu(
    archives: list[dict],
    *,
    refetch_rapports: bool = True,
) -> tuple[int, int, int]:
    """
    Recalcule financier + profit sur les archives PMU déjà évaluées.
    Retourne (nb_mises_a_jour, nb_rapports_injectes, nb_ignorees).
    """
    from pmu_rapports_definitifs import injecter_rapport_definitif_archive

    maj = 0
    rapports_ok = 0
    ignorees = 0
    for arch in archives:
        if arch.get("reussi_pmu") is None:
            ignorees += 1
            continue
        if str(arch.get("statut") or "") == "En attente":
            ignorees += 1
            continue
        if refetch_rapports and injecter_rapport_definitif_archive(arch):
            rapports_ok += 1
        enrichir_archive_pmu_financier(arch)
        maj += 1
    return maj, rapports_ok, ignorees


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


def _fusionner_blocs_actifs(pmu: dict, foot: dict) -> dict[str, Any]:
    """Agrégat global : uniquement les sports avec au moins une résolution."""
    blocs: list[tuple[str, dict]] = []
    if int(pmu.get("total") or 0) > 0:
        blocs.append(("pmu", pmu))
    if int(foot.get("total") or 0) > 0:
        blocs.append(("foot", foot))
    if not blocs:
        return {
            "taux": 0,
            "victoires": 0,
            "total": 0,
            "mises_cumulees": 0.0,
            "profit_net": 0.0,
            "roi_pct": 0.0,
        }
    if len(blocs) == 1:
        return dict(blocs[0][1])
    vict = sum(int(b.get("victoires") or 0) for _, b in blocs)
    tot = sum(int(b.get("total") or 0) for _, b in blocs)
    mises = sum(float(b.get("mises_cumulees") or 0) for _, b in blocs)
    profit = sum(float(b.get("profit_net") or 0) for _, b in blocs)
    return {
        "taux": round(vict / tot * 100) if tot else 0,
        "victoires": vict,
        "total": tot,
        "mises_cumulees": round(mises, 2),
        "profit_net": round(profit, 2),
        "roi_pct": round((profit / mises) * 100, 1) if mises > 0 else 0.0,
    }


def _bloc_communaute_enrichi(source: dict) -> dict[str, Any]:
    """Conserve les champs détaillés (historique, calibration…) en plus des KPI."""
    out = bloc_communaute_depuis_stats(source)
    for key in (
        "detail_par_type_pari",
        "detail_par_marche",
        "calibration",
        "historique_matchs",
        "historique_courses",
        "compteur_historique",
        "gains_par_famille",
        "reussites_par_type",
        "membres_actifs",
        "mise_unitaire",
        "matchs_termines",
        "taux_reussite_plateforme",
    ):
        if key in source and source[key] is not None:
            out[key] = source[key]
    return out


def fusionner_blocs_sports(pmu: dict, foot: dict) -> dict[str, Any]:
    """Score global : sports actifs uniquement (évite 100 % Foot vide + PMU réel)."""
    global_bloc = _fusionner_blocs_actifs(pmu, foot)
    return {
        "pmu": _bloc_communaute_enrichi(pmu),
        "foot": _bloc_communaute_enrichi(foot),
        "global": bloc_communaute_depuis_stats(global_bloc),
    }
