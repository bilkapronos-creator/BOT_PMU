"""
Résolution des matchs Foot archivés en EN_ATTENTE :
  1. Récupération du score (Winamax puis repli TheSportsDB)
  2. Validation mathématique du conseil Velora (valider_foot)
  3. Mise à jour velora_archives_foot.json (GAGNANT / PERDANT + score_final)

Usage :
  python resolver_scores_foot.py
  (ou via run_all.py / velora_archiver_foot.py)
"""
from __future__ import annotations

import sys

from velora_archiver_foot import resoudre_matchs_en_attente


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("[resolver-foot] Résolution des archives EN_ATTENTE…")
    stats = resoudre_matchs_en_attente()
    print(
        f"[resolver-foot] {stats.get('resolus', 0)} résolu(s), "
        f"{stats.get('encore_attente', 0)} encore en attente, "
        f"{stats.get('scores_recuperes', 0)} score(s) récupéré(s)"
    )
    if stats.get("erreur"):
        print(f"[resolver-foot] Erreur : {stats['erreur']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
