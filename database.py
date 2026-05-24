"""
Façade persistance archives Velora.

- ARCHIVES_BACKEND=supabase + SUPABASE_SERVICE_ROLE_KEY → Supabase (prod Render)
- ARCHIVES_BACKEND=sqlite ou variables Supabase absentes → SQLite local
"""

import os
from typing import Optional

from archives_common import ARCHIVES_MAX_PER_USER, DB_PATH  # noqa: F401 — réexport
from archives_common import course_key as _course_key

_backend: Optional[str] = None
_impl = None


def _resoudre_backend() -> str:
    global _backend
    if _backend is not None:
        return _backend

    force = (os.environ.get("ARCHIVES_BACKEND") or "auto").strip().lower()
    has_supabase = bool(
        (os.environ.get("SUPABASE_URL") or "").strip()
        and (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    )

    if force == "sqlite":
        _backend = "sqlite"
    elif force == "supabase":
        if not has_supabase:
            raise RuntimeError(
                "ARCHIVES_BACKEND=supabase mais SUPABASE_URL ou "
                "SUPABASE_SERVICE_ROLE_KEY manquant.",
            )
        _backend = "supabase"
    elif has_supabase:
        _backend = "supabase"
    else:
        _backend = "sqlite"

    return _backend


def _store():
    global _impl
    if _impl is not None:
        return _impl

    if _resoudre_backend() == "supabase":
        import database_supabase as impl
    else:
        import database_sqlite as impl

    _impl = impl
    return _impl


def init_db() -> None:
    _store().init_db()


def sauvegarder_archive(user_id: str, archive: dict) -> dict:
    return _store().sauvegarder_archive(user_id, archive)


def lister_archives(user_id: str, limit: Optional[int] = None) -> list:
    return _store().lister_archives(user_id, limit=limit)


def obtenir_archive(user_id: str, archive_id) -> Optional[dict]:
    return _store().obtenir_archive(user_id, archive_id)


def compter_archives_plateforme() -> dict:
    return _store().compter_archives_plateforme()


def lister_toutes_archives(limit: int = 10000) -> list:
    return _store().lister_toutes_archives(limit=limit)
