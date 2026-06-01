"""
Résolution batch des archives PMU « En attente » :
interroge l'API PMU (ordreArrivee), réévalue le pronostic, persiste Supabase/SQLite.

Usage :
  cd web
  python resolver_pmu_archives.py
  python resolver_pmu_archives.py --user-id <uuid>
  python resolver_pmu_archives.py --financier-only   # recalcule profit/ROI sans PMU
  python resolver_pmu_archives.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))

from database import init_db, lister_archives, lister_toutes_archives, sauvegarder_archive
from stats_pmu import (
    _est_en_attente_arrivee,
    _est_statut_orphelin,
    _est_terminee,
)
from pmu_rapports_definitifs import injecter_rapport_definitif_archive
from velora_finance import recalculer_financier_archives_pmu
from velora_resilience import ErreurReseauExterne, pmu_get

# Import logique d'évaluation (même code que POST /evaluation)
from api_pmu import (  # noqa: E402
    HEADERS_PMU,
    construire_archive_complete,
    evaluer_pronostic_pmu,
    normaliser_code_pmu,
    normaliser_date_pmu,
    _metadata_course_pmu,
    _url_course_pmu,
)


def _fetch_arrivee_pmu(date_api: str, reunion: str, course: str) -> tuple[list[int], dict]:
    date_pmu = normaliser_date_pmu(str(date_api))
    reunion_pmu = normaliser_code_pmu(str(reunion), "R")
    course_pmu = normaliser_code_pmu(str(course), "C")
    try:
        response = pmu_get(
            _url_course_pmu(date_pmu, reunion_pmu, course_pmu),
            headers=HEADERS_PMU,
        )
    except ErreurReseauExterne as exc:
        return [], {"erreur": str(exc)}
    if response.status_code != 200:
        return [], {"erreur": f"HTTP {response.status_code}"}
    data = response.json()
    ordre_brut = data.get("ordreArrivee") or []
    ordre = [int(a[0]) for a in ordre_brut if a]
    meta = _metadata_course_pmu(date_pmu, reunion_pmu, course_pmu)
    return ordre[:5], meta


def resoudre_une_archive(archive: dict, *, dry_run: bool = False) -> str:
    """
    Retourne : resolu | attente | erreur | skip
    """
    user_id = str(archive.get("user_id") or "").strip()
    date_api = archive.get("dateApi") or archive.get("date_api")
    reunion = archive.get("reunion")
    course = archive.get("course")
    if not user_id or not date_api or not reunion or not course:
        return "skip"

    arrivee, meta = _fetch_arrivee_pmu(str(date_api), str(reunion), str(course))
    if meta.get("erreur"):
        print(f"  · {reunion}/{course} ({date_api}) : {meta['erreur']}")
        return "erreur"

    if not arrivee:
        return "attente"

    if dry_run:
        print(f"  · {reunion}/{course} ({date_api}) → arrivée {arrivee} (dry-run)")
        return "resolu"

    archive_enrichie = {
        **archive,
        "nombre_partants": meta.get("nombre_partants") or archive.get("nombre_partants"),
        "est_quinte": meta.get("est_quinte", archive.get("est_quinte", False)),
    }
    evaluation = evaluer_pronostic_pmu(archive_enrichie, arrivee)
    evaluation["terminee"] = True
    evaluation["arrivee_officielle"] = arrivee
    evaluation["gagnant"] = arrivee[0]

    archive_pour_fin = {
        **archive_enrichie,
        **evaluation,
        "arrivee_officielle": arrivee,
    }
    injecter_rapport_definitif_archive(archive_pour_fin, evaluation)

    complete = construire_archive_complete(user_id, archive_pour_fin, evaluation, meta)
    sauvegarder_archive(user_id, complete)
    label = complete.get("type_pari_pmu") or "?"
    print(
        f"  · {reunion}/{course} ({date_api}) → {arrivee[0]}-{arrivee[1] if len(arrivee) > 1 else '?'}-… "
        f"| {label} | rapport={complete.get('rapport_pmu')} "
        f"| profit={complete.get('financier', {}).get('profit')}"
    )
    return "resolu"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Résolution archives PMU en attente")
    parser.add_argument("--user-id", help="Limiter à un membre (UUID Supabase)")
    parser.add_argument("--dry-run", action="store_true", help="Ne pas écrire en base")
    parser.add_argument(
        "--financier-only",
        action="store_true",
        help="Recalcule uniquement financier/ROI sur archives déjà évaluées",
    )
    parser.add_argument("--pause", type=float, default=0.35, help="Pause entre appels PMU (s)")
    args = parser.parse_args()

    try:
        init_db()
    except Exception as exc:
        print(f"[resolver-pmu] Init base : {exc}")
        return 1

    if args.financier_only:
        if args.user_id:
            archives = lister_archives(args.user_id, limit=500)
        else:
            archives = lister_toutes_archives()
        maj, rapports_ok, ignorees = recalculer_financier_archives_pmu(archives)
        if not args.dry_run:
            for arch in archives:
                if arch.get("reussi_pmu") is None or arch.get("statut") == "En attente":
                    continue
                uid = str(arch.get("user_id") or "").strip()
                if uid:
                    sauvegarder_archive(uid, arch)
        print(
            f"[resolver-pmu] Financier recalculé : {maj} archive(s), "
            f"{rapports_ok} rapport(s) définitif(s), {ignorees} ignorée(s)"
        )
        return 0

    if args.user_id:
        archives = lister_archives(args.user_id, limit=500)
    else:
        archives = lister_toutes_archives()

    total = len(archives)
    attente = [a for a in archives if _est_en_attente_arrivee(a) or _est_statut_orphelin(a)]
    deja = sum(1 for a in archives if _est_terminee(a))

    print(f"[resolver-pmu] Archives totales : {total}")
    print(f"[resolver-pmu] Déjà résolues (arrivée + reussi_pmu) : {deja}")
    print(f"[resolver-pmu] À interroger (en attente / orphelines) : {len(attente)}")

    stats = {"resolu": 0, "attente": 0, "erreur": 0, "skip": 0}
    for arch in attente:
        code = resoudre_une_archive(arch, dry_run=args.dry_run)
        stats[code] = stats.get(code, 0) + 1
        if code != "skip":
            time.sleep(max(0.0, args.pause))

    print(
        f"[resolver-pmu] Terminé : {stats['resolu']} résolu(s), "
        f"{stats['attente']} toujours sans arrivée, "
        f"{stats['erreur']} erreur(s), {stats['skip']} ignorée(s)"
    )
    if stats["resolu"] and not args.dry_run:
        print("[resolver-pmu] Lancez : python publish_communaute.py  (met à jour ROI / vitrine)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
