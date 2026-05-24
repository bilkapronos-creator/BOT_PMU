#!/usr/bin/env python3
"""
Migre les archives SQLite (velora_engine.db) vers Supabase (velora_member_archives).

Prérequis :
  1. Exécuter supabase/velora_archives.sql dans Supabase
  2. .env à la racine avec SUPABASE_URL et SUPABASE_SERVICE_ROLE_KEY

Usage :
  python scripts/migrate_sqlite_to_supabase.py
  python scripts/migrate_sqlite_to_supabase.py --dry-run
  python scripts/migrate_sqlite_to_supabase.py --db chemin/vers/velora_engine.db
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _normaliser_supabase_url(url: str) -> str:
    return url.strip().replace("/rest/v1", "").rstrip("/")


def charger_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for nom in (".env.local", ".env"):
        fichier = ROOT / nom
        if fichier.exists():
            load_dotenv(fichier)
    brut = os.environ.get("SUPABASE_URL", "")
    if brut:
        os.environ["SUPABASE_URL"] = _normaliser_supabase_url(brut)


def lire_lignes_sqlite(db_path: Path) -> list[dict]:
    if not db_path.is_file():
        raise FileNotFoundError(f"Base SQLite introuvable : {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        if "archives" not in tables:
            raise RuntimeError(f"Table « archives » absente dans {db_path}")

        rows = conn.execute(
            """
            SELECT id, user_id, course_key, payload, created_at
            FROM archives
            ORDER BY user_id, created_at ASC, id ASC
            """,
        ).fetchall()
    finally:
        conn.close()

    result = []
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            print(f"[SKIP] id={row['id']} : payload JSON invalide", file=sys.stderr)
            continue
        if not isinstance(payload, dict):
            continue
        result.append(
            {
                "sqlite_id": row["id"],
                "user_id": str(row["user_id"] or "").strip(),
                "course_key": str(row["course_key"] or "").strip(),
                "payload": payload,
                "created_at": row["created_at"],
            },
        )
    return result


def migrer(lignes: list[dict], dry_run: bool) -> int:
    from archives_common import course_key as ck
    from database_supabase import init_db, sauvegarder_archive

    if dry_run:
        par_user: dict[str, int] = {}
        for ligne in lignes:
            uid = ligne["user_id"]
            par_user[uid] = par_user.get(uid, 0) + 1
        print(f"[dry-run] {len(lignes)} archive(s) à migrer")
        for uid, n in sorted(par_user.items(), key=lambda x: -x[1]):
            print(f"  · {uid[:8]}… : {n}")
        return 0

    os.environ.setdefault("ARCHIVES_BACKEND", "supabase")
    init_db()

    ok = 0
    erreurs = 0
    for ligne in lignes:
        uid = ligne["user_id"]
        if not uid:
            print(f"[SKIP] sqlite_id={ligne['sqlite_id']} : user_id vide", file=sys.stderr)
            erreurs += 1
            continue

        archive = {**ligne["payload"], "user_id": uid}
        cle = ck(archive)
        if not cle or cle == "--":
            print(
                f"[SKIP] sqlite_id={ligne['sqlite_id']} : course_key invalide ({cle!r})",
                file=sys.stderr,
            )
            erreurs += 1
            continue

        try:
            sauvegarder_archive(uid, archive)
            ok += 1
            print(f"[OK] {uid[:8]}… · {cle}")
        except Exception as exc:
            erreurs += 1
            print(f"[ERR] {uid[:8]}… · {cle} — {exc}", file=sys.stderr)

    print(f"\nTerminé : {ok} migrée(s), {erreurs} ignorée(s) / en erreur.")
    return 0 if erreurs == 0 else 1


def main() -> int:
    charger_env()

    parser = argparse.ArgumentParser(description="Migration SQLite → Supabase (archives Velora)")
    parser.add_argument(
        "--db",
        type=Path,
        default=ROOT / os.environ.get("VELORA_DB_PATH", "velora_engine.db"),
        help="Chemin vers velora_engine.db",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Affiche le volume sans écrire dans Supabase",
    )
    args = parser.parse_args()

    if not args.dry_run:
        if not (os.environ.get("SUPABASE_URL") or "").strip():
            print("SUPABASE_URL manquant dans .env", file=sys.stderr)
            return 1
        if not (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip():
            print("SUPABASE_SERVICE_ROLE_KEY manquant dans .env", file=sys.stderr)
            return 1

    try:
        lignes = lire_lignes_sqlite(args.db.resolve())
    except (FileNotFoundError, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 1

    if not lignes:
        print(f"Aucune archive dans {args.db}")
        return 0

    return migrer(lignes, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
