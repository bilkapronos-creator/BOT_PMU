"""
Résolution des matchs Foot archivés en EN_ATTENTE :
  1. Récupération du score (Winamax puis repli TheSportsDB)
  2. Validation mathématique du conseil Velora (valider_foot)
  3. Mise à jour web/velora_archives_foot.json (GAGNANT / PERDANT + score_final)

Usage :
  python resolver_scores_foot.py
  (ou via run_all.py / velora_archiver_foot.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent

from velora_archiver_foot import (  # noqa: E402
    ARCHIVES_FOOT_PATH,
    debug_etat_archives_foot,
    resoudre_matchs_en_attente,
)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("[resolver-foot] === Résolution archives Foot ===")
    print(f"[resolver-foot] Dossier web     : {WEB_DIR}")
    print(f"[resolver-foot] Fichier cible  : {ARCHIVES_FOOT_PATH.resolve()}")
    print(f"[resolver-foot] Existe sur disque : {ARCHIVES_FOOT_PATH.is_file()}")

    dbg = debug_etat_archives_foot()
    print(
        f"[resolver-foot] Synthèse : {dbg['total']} match(s) JSON, "
        f"{dbg['en_attente']} EN_ATTENTE, {dbg['pret_pour_score']} à résoudre maintenant"
    )

    stats = resoudre_matchs_en_attente()
    cascade = stats.get("cascade") or {}
    print(
        f"[resolver-foot] Résultat : {stats.get('resolus', 0)} résolu(s), "
        f"{stats.get('encore_attente', 0)} encore en attente, "
        f"{stats.get('scores_recuperes', 0)} score(s) récupéré(s)"
    )
    if cascade:
        print(
            f"[resolver-foot] Sources : Winamax {cascade.get('winamax', 0)}, "
            f"TheSportsDB {cascade.get('thesportsdb', 0)}, "
            f"Scraper {cascade.get('scraper', 0)}"
        )
    if stats.get("erreur"):
        print(f"[resolver-foot] Erreur : {stats['erreur']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
