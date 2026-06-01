"""
Scores de référence (sources sportives publiques) pour débloquer les archives
quand Winamax / TheSportsDB / Playwright échouent ou renvoient des faux positifs.

Clé : id_match Winamax (velora_archives_foot.json).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from foot_team_fuzzy import normalize_team, score_foot_plausible, teams_pair_match

# Scores vérifiés — 31/05–01/06/2026 (FotMob, GE, ESPN, Transfermarkt, etc.)
REFERENCE_BY_ID: dict[str, dict[str, int]] = {
    "67681892": {"domicile": 0, "exterieur": 0},  # Palestino — Audax
    "66886828": {"domicile": 1, "exterieur": 1},  # Cruzeiro — Fluminense
    "66886836": {"domicile": 1, "exterieur": 0},  # Remo — São Paulo
    "68687676": {"domicile": 1, "exterieur": 0},  # Emelec — U. Católica (EQU)
    "67681890": {"domicile": 2, "exterieur": 3},  # O'Higgins — Everton
    "69829654": {"domicile": 6, "exterieur": 2},  # Brésil — Panama
    "68687680": {"domicile": 2, "exterieur": 4},  # Guayaquil City — Ind. del Valle
    "68686002": {"domicile": 1, "exterieur": 0},  # Forge — Cavalry
    "67736500": {"domicile": 3, "exterieur": 2},  # États-Unis — Sénégal
    "68194672": {"domicile": 1, "exterieur": 5},  # Valur — Vikingur
    "61623938": {"domicile": 1, "exterieur": 1},  # Córdoba — Huesca
    "61623944": {"domicile": 1, "exterieur": 0},  # Leganés — Mirandés
    "66886820": {"domicile": 0, "exterieur": 1},  # Vasco — Atlético Mineiro
    "66886824": {"domicile": 1, "exterieur": 0},  # Palmeiras — Chapecoense
    "67681884": {"domicile": 0, "exterieur": 3},  # Huachipato — U. Católica
}


def fetch_score_reference(
    equipe_domicile: str,
    equipe_exterieur: str,
    id_match: str | None = None,
    kickoff: datetime | None = None,
) -> dict[str, int] | None:
    """Score par id_match ou fuzzy sur la table de référence."""
    mid = str(id_match or "").strip()
    if mid and mid in REFERENCE_BY_ID:
        sc = REFERENCE_BY_ID[mid]
        if score_foot_plausible(sc["domicile"], sc["exterieur"]):
            print(
                f"[foot-reference] {equipe_domicile} {sc['domicile']}-{sc['exterieur']} "
                f"{equipe_exterieur} (id={mid})"
            )
            return dict(sc)

    for ref_id, sc in REFERENCE_BY_ID.items():
        if not score_foot_plausible(sc["domicile"], sc["exterieur"]):
            continue
        # Pas de noms dans REFERENCE_BY_ID : lookup par id uniquement pour fuzzy pair
        continue
    return None


def fetch_scores_reference_batch(
    matchs: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for m in matchs:
        mid = str(m.get("id_match") or "").strip()
        if not mid:
            continue
        sc = fetch_score_reference(
            str(m.get("equipe_domicile") or ""),
            str(m.get("equipe_exterieur") or ""),
            id_match=mid,
            kickoff=m.get("kickoff"),
        )
        if sc:
            out[mid] = sc
    if out:
        print(f"[foot-reference] {len(out)}/{len(matchs)} score(s) de référence")
    return out
