"""Persistance des archives Velora par membre (SQLite)."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(os.environ.get("VELORA_DB_PATH", "velora_engine.db"))
ARCHIVES_MAX_PER_USER = int(os.environ.get("ARCHIVES_MAX_PER_USER", "50"))


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def init_db() -> None:
    """Initialise velora_engine.db et la table archives."""
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS archives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                course_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, course_key)
            )
            """
        )
        _migrer_schema_archives(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_archives_user_id ON archives(user_id)"
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_archives_user_created
            ON archives(user_id, created_at DESC)
            """
        )


def _migrer_schema_archives(conn: sqlite3.Connection) -> None:
    """Supprime l'ancienne colonne updated_at si la base a été créée avant la migration."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(archives)").fetchall()}
    if "updated_at" not in cols:
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS archives_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            course_key TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, course_key)
        )
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO archives_new (id, user_id, course_key, payload, created_at)
        SELECT id, user_id, course_key, payload,
               COALESCE(
                   datetime(created_at, 'unixepoch'),
                   datetime(updated_at, 'unixepoch'),
                   datetime('now')
               )
        FROM archives
        """
    )
    conn.execute("DROP TABLE archives")
    conn.execute("ALTER TABLE archives_new RENAME TO archives")


def _course_key(archive: dict) -> str:
    date_api = archive.get("dateApi") or archive.get("date_api") or ""
    reunion = archive.get("reunion") or ""
    course = archive.get("course") or ""
    return f"{date_api}-{reunion}-{course}"


def _row_to_archive(row: sqlite3.Row) -> dict:
    data = json.loads(row["payload"])
    data["id"] = row["id"]
    data["user_id"] = row["user_id"]
    data["created_at"] = row["created_at"]
    return data


def sauvegarder_archive(user_id: str, archive: dict) -> dict:
    """
    Crée ou met à jour l'archive d'un membre.
    Isolation stricte : toutes les écritures sont scopées par user_id.
    """
    user_id = str(user_id).strip()
    if not user_id:
        raise ValueError("user_id requis")

    course_key = _course_key(archive)
    if not course_key or course_key == "--":
        raise ValueError("Archive invalide : dateApi, reunion et course requis")

    archive = {**archive, "user_id": user_id}
    now = _now_ts()
    archive["timestamp"] = int(datetime.now(timezone.utc).timestamp() * 1000)

    payload = json.dumps(archive, ensure_ascii=False)

    with get_conn() as conn:
        existing = conn.execute(
            """
            SELECT id FROM archives
            WHERE user_id = ? AND course_key = ?
            """,
            (user_id, course_key),
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE archives
                SET payload = ?, created_at = ?
                WHERE user_id = ? AND course_key = ?
                """,
                (payload, now, user_id, course_key),
            )
            archive_id = existing["id"]
        else:
            cur = conn.execute(
                """
                INSERT INTO archives (user_id, course_key, payload, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, course_key, payload, now),
            )
            archive_id = cur.lastrowid

        _appliquer_limite_archives(conn, user_id)

    archive["id"] = archive_id
    archive["created_at"] = now
    return archive


def _appliquer_limite_archives(conn: sqlite3.Connection, user_id: str) -> None:
    """Conserve au maximum ARCHIVES_MAX_PER_USER entrées par membre."""
    rows = conn.execute(
        """
        SELECT id FROM archives
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        (user_id,),
    ).fetchall()
    if len(rows) <= ARCHIVES_MAX_PER_USER:
        return
    ids_a_supprimer = [r["id"] for r in rows[ARCHIVES_MAX_PER_USER:]]
    placeholders = ",".join("?" * len(ids_a_supprimer))
    conn.execute(
        f"""
        DELETE FROM archives
        WHERE user_id = ? AND id IN ({placeholders})
        """,
        (user_id, *ids_a_supprimer),
    )


def lister_archives(user_id: str, limit: Optional[int] = None) -> list:
    """
    Liste les archives d'un seul membre.
    Impossible d'obtenir les archives d'un autre user_id via cette fonction.
    """
    user_id = str(user_id).strip()
    if not user_id:
        return []

    lim = min(limit or ARCHIVES_MAX_PER_USER, ARCHIVES_MAX_PER_USER)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, payload, created_at
            FROM archives
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, lim),
        ).fetchall()
    return [_row_to_archive(r) for r in rows]


def obtenir_archive(user_id: str, archive_id: int) -> Optional[dict]:
    """Récupère une archive uniquement si elle appartient au membre."""
    user_id = str(user_id).strip()
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT id, user_id, payload, created_at
            FROM archives
            WHERE id = ? AND user_id = ?
            """,
            (archive_id, user_id),
        ).fetchone()
    return _row_to_archive(row) if row else None


def compter_archives_plateforme() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM archives").fetchone()["n"]
        membres = conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS n FROM archives"
        ).fetchone()["n"]
    return {"total_archives": total, "membres_actifs": membres}


def lister_toutes_archives(limit: int = 10000) -> list:
    """Usage interne : statistiques publiques anonymisées uniquement."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, payload, created_at
            FROM archives
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_archive(r) for r in rows]
