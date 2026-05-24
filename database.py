"""Persistance des archives Velora par membre (SQLite)."""

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(os.environ.get("VELORA_DB_PATH", "velora_engine.db"))
ARCHIVES_MAX_PER_USER = int(os.environ.get("ARCHIVES_MAX_PER_USER", "50"))


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS archives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                course_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(user_id, course_key)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_archives_user_id ON archives(user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_archives_updated ON archives(updated_at DESC)"
        )


def _course_key(archive: dict) -> str:
    date_api = archive.get("dateApi") or archive.get("date_api") or ""
    reunion = archive.get("reunion") or ""
    course = archive.get("course") or ""
    return f"{date_api}-{reunion}-{course}"


def _row_to_archive(row: sqlite3.Row) -> dict:
    data = json.loads(row["payload"])
    data["id"] = row["id"]
    data["user_id"] = row["user_id"]
    return data


def sauvegarder_archive(user_id: str, archive: dict) -> dict:
    """Crée ou met à jour l'archive d'un membre (isolation par user_id)."""
    user_id = str(user_id).strip()
    course_key = _course_key(archive)
    if not course_key or course_key == "--":
        raise ValueError("Archive invalide : dateApi, reunion et course requis")

    archive = {**archive, "user_id": user_id}
    now = time.time()

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id, created_at FROM archives WHERE user_id = ? AND course_key = ?",
            (user_id, course_key),
        ).fetchone()

        created_at = existing["created_at"] if existing else now
        archive["timestamp"] = int(now * 1000)

        payload = json.dumps(archive, ensure_ascii=False)

        if existing:
            conn.execute(
                """
                UPDATE archives SET payload = ?, updated_at = ?
                WHERE user_id = ? AND course_key = ?
                """,
                (payload, now, user_id, course_key),
            )
            archive_id = existing["id"]
        else:
            cur = conn.execute(
                """
                INSERT INTO archives (user_id, course_key, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, course_key, payload, created_at, now),
            )
            archive_id = cur.lastrowid

        _appliquer_limite_archives(conn, user_id)

    archive["id"] = archive_id
    return archive


def _appliquer_limite_archives(conn: sqlite3.Connection, user_id: str) -> None:
    rows = conn.execute(
        """
        SELECT id FROM archives
        WHERE user_id = ?
        ORDER BY updated_at DESC
        """,
        (user_id,),
    ).fetchall()
    if len(rows) <= ARCHIVES_MAX_PER_USER:
        return
    ids_a_supprimer = [r["id"] for r in rows[ARCHIVES_MAX_PER_USER:]]
    placeholders = ",".join("?" * len(ids_a_supprimer))
    conn.execute(
        f"DELETE FROM archives WHERE id IN ({placeholders}) AND user_id = ?",
        (*ids_a_supprimer, user_id),
    )


def lister_archives(user_id: str, limit: Optional[int] = None) -> list:
    """Retourne uniquement les archives du membre connecté."""
    user_id = str(user_id).strip()
    lim = limit or ARCHIVES_MAX_PER_USER
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, payload, updated_at
            FROM archives
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (user_id, lim),
        ).fetchall()
    return [_row_to_archive(r) for r in rows]


def obtenir_archive(user_id: str, archive_id: int) -> Optional[dict]:
    """Récupère une archive si elle appartient au membre."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, user_id, payload FROM archives WHERE id = ? AND user_id = ?",
            (archive_id, str(user_id).strip()),
        ).fetchone()
    return _row_to_archive(row) if row else None


def compter_archives_par_user() -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, COUNT(*) AS n FROM archives GROUP BY user_id"
        ).fetchall()
    return {r["user_id"]: r["n"] for r in rows}


def lister_toutes_archives(limit: int = 10000) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, user_id, payload FROM archives ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_archive(r) for r in rows]
