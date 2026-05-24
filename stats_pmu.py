"""Statistiques membres et agrégats publics (vitrine)."""

from typing import Any

from database import lister_archives, lister_toutes_archives


def _est_terminee(archive: dict) -> bool:
    return archive.get("statut") not in (None, "En attente") and archive.get(
        "reussi_pmu"
    ) is not None


def _est_victoire(archive: dict) -> bool:
    if not _est_terminee(archive):
        return False
    if archive.get("reussi_pmu") is not True:
        return False
    type_pari = archive.get("type_pari_pmu")
    return type_pari not in (None, "", "Perdu")


def get_stats_utilisateur(user_id: str) -> dict:
    """Performances globales d'un membre (hors paris « Perdu » pour les victoires)."""
    archives = lister_archives(user_id, limit=500)
    total = len(archives)
    terminees = [a for a in archives if _est_terminee(a)]
    victoires = [a for a in terminees if _est_victoire(a)]

    taux_global = (
        round(len(victoires) / len(terminees) * 100) if terminees else 0
    )

    par_type: dict[str, dict[str, Any]] = {}
    for archive in terminees:
        label = archive.get("type_pari_pmu") or "Indéterminé"
        if label == "Perdu":
            continue
        if label not in par_type:
            par_type[label] = {"total": 0, "succes": 0, "taux": 0}
        par_type[label]["total"] += 1
        if archive.get("reussi_pmu") is True:
            par_type[label]["succes"] += 1

    for stats in par_type.values():
        stats["taux"] = (
            round(stats["succes"] / stats["total"] * 100) if stats["total"] else 0
        )

    succes_par_type = {
        label: data["succes"] for label, data in par_type.items() if data["succes"] > 0
    }

    return {
        "user_id": user_id,
        "total_courses_analysees": total,
        "courses_terminees": len(terminees),
        "victoires": len(victoires),
        "taux_reussite_global": taux_global,
        "succes_par_type_pari": succes_par_type,
        "detail_par_type_pari": par_type,
    }


def get_stats_publiques() -> dict:
    """
    Données agrégées anonymisées pour la vitrine (aucun détail par utilisateur).
    """
    archives = lister_toutes_archives()
    total_courses = len(archives)
    terminees = sum(1 for a in archives if _est_terminee(a))
    victoires = sum(1 for a in archives if _est_victoire(a))
    membres_actifs = len({a.get("user_id") for a in archives if a.get("user_id")})

    taux_plateforme = (
        round(victoires / terminees * 100) if terminees else 0
    )

    gains_par_famille: dict[str, int] = {}
    for archive in archives:
        if not _est_victoire(archive):
            continue
        label = archive.get("type_pari_pmu") or "Autre"
        famille = label.split()[0] if label else "Autre"
        gains_par_famille[famille] = gains_par_famille.get(famille, 0) + 1

    return {
        "total_courses_plateforme": total_courses,
        "courses_terminees_plateforme": terminees,
        "victoires_plateforme": victoires,
        "taux_reussite_plateforme": taux_plateforme,
        "membres_actifs": membres_actifs,
        "gains_par_famille_pari": gains_par_famille,
        "reussites_par_type_pari": _agreger_reussites_par_type(archives),
    }


def _agreger_reussites_par_type(archives: list) -> dict:
    """Comptage anonymisé des types de paris gagnants (sans user_id)."""
    compteur: dict[str, int] = {}
    for archive in archives:
        if not _est_victoire(archive):
            continue
        label = archive.get("type_pari_pmu") or "Autre"
        compteur[label] = compteur.get(label, 0) + 1
    return compteur
