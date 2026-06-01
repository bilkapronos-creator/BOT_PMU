"""Persistance des archives Velora via Supabase (service_role côté API Render)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from archives_common import ARCHIVES_MAX_PER_USER, course_key as _course_key
from velora_resilience import (
    ArchivesStorageError,
    options_client_supabase,
    supabase_execute,
)

_TABLE = "velora_member_archives"
_client = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_client():
    global _client
    if _client is not None:
        return _client

    url = (os.environ.get("SUPABASE_URL") or "").strip()
    url = url.replace("/rest/v1", "").rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise RuntimeError(
            "Supabase archives : SUPABASE_URL et SUPABASE_SERVICE_ROLE_KEY requis sur Render.",
        )

    try:
        from supabase import create_client
    except ImportError as exc:
        raise RuntimeError(
            "Paquet « supabase » manquant. Ajoutez-le à requirements.txt.",
        ) from exc

    options = options_client_supabase()
    _client = create_client(url, key, options) if options else create_client(url, key)
    return _client


def _payload_to_archive(row: dict) -> dict:
    data = dict(row.get("payload") or {})
    data["id"] = row.get("id")
    data["user_id"] = row.get("user_id")
    data["created_at"] = row.get("created_at")
    if row.get("updated_at"):
        data["updated_at"] = row.get("updated_at")
    return data


def init_db() -> None:
    """Vérifie la connexion Supabase (no-op si OK)."""
    supabase_execute(
        lambda: _get_client().table(_TABLE).select("id", count="exact").limit(1).execute(),
        description="init_db velora_member_archives",
    )


def _appliquer_limite_archives(user_id: str) -> None:
    client = _get_client()

    def _lire():
        return (
            client.table(_TABLE)
            .select("id, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .order("id", desc=True)
            .execute()
        )

    resp = supabase_execute(_lire, description="liste archives pour limite")
    rows = resp.data or []
    if len(rows) <= ARCHIVES_MAX_PER_USER:
        return
    for row in rows[ARCHIVES_MAX_PER_USER:]:
        supabase_execute(
            lambda rid=row["id"]: client.table(_TABLE).delete().eq("id", rid).execute(),
            description="suppression archive excédentaire",
        )


def sauvegarder_archive(user_id: str, archive: dict) -> dict:
    user_id = str(user_id).strip()
    if not user_id:
        raise ValueError("user_id requis")

    course_key = _course_key(archive)
    if not course_key or course_key == "--":
        raise ValueError("Archive invalide : dateApi, reunion et course requis")

    archive = {**archive, "user_id": user_id}
    now = _now_iso()
    archive["timestamp"] = int(datetime.now(timezone.utc).timestamp() * 1000)

    client = _get_client()
    row = {
        "user_id": user_id,
        "course_key": course_key,
        "payload": archive,
        "updated_at": now,
    }

    def _lire_existante():
        return (
            client.table(_TABLE)
            .select("id, created_at")
            .eq("user_id", user_id)
            .eq("course_key", course_key)
            .limit(1)
            .execute()
        )

    existing = supabase_execute(_lire_existante, description="lecture archive existante")

    if existing.data:
        archive_id = existing.data[0]["id"]
        created_at = existing.data[0].get("created_at") or now
        supabase_execute(
            lambda: client.table(_TABLE)
            .update({"payload": archive, "updated_at": now})
            .eq("id", archive_id)
            .execute(),
            description="mise à jour archive",
        )
    else:
        row["created_at"] = now

        def _inserer():
            return client.table(_TABLE).insert(row).execute()

        ins = supabase_execute(_inserer, description="insertion archive")
        if not ins.data:
            raise ArchivesStorageError("Échec insertion archive Supabase (réponse vide)")
        archive_id = ins.data[0]["id"]
        created_at = ins.data[0].get("created_at") or now

    _appliquer_limite_archives(user_id)

    archive["id"] = archive_id
    archive["created_at"] = created_at
    archive["updated_at"] = now
    return archive


def lister_archives(user_id: str, limit: Optional[int] = None) -> list:
    user_id = str(user_id).strip()
    if not user_id:
        return []

    lim = min(limit or ARCHIVES_MAX_PER_USER, ARCHIVES_MAX_PER_USER)

    def _lire():
        return (
            _get_client()
            .table(_TABLE)
            .select("id, user_id, payload, created_at, updated_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .order("id", desc=True)
            .limit(lim)
            .execute()
        )

    resp = supabase_execute(_lire, description="liste archives membre")
    return [_payload_to_archive(r) for r in (resp.data or [])]


def obtenir_archive(user_id: str, archive_id) -> Optional[dict]:
    user_id = str(user_id).strip()

    def _lire():
        return (
            _get_client()
            .table(_TABLE)
            .select("id, user_id, payload, created_at, updated_at")
            .eq("id", str(archive_id))
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )

    resp = supabase_execute(_lire, description="lecture archive par id")
    if not resp.data:
        return None
    return _payload_to_archive(resp.data[0])


def compter_archives_plateforme() -> dict:
    client = _get_client()

    def _total():
        return client.table(_TABLE).select("id", count="exact").limit(1).execute()

    def _users():
        return client.table(_TABLE).select("user_id").execute()

    total_resp = supabase_execute(_total, description="comptage archives")
    users_resp = supabase_execute(_users, description="liste user_id archives")
    total = total_resp.count or 0
    membres = len({r["user_id"] for r in (users_resp.data or []) if r.get("user_id")})
    return {"total_archives": total, "membres_actifs": membres}


def lister_toutes_archives(limit: int = 10000) -> list:
    def _lire():
        return (
            _get_client()
            .table(_TABLE)
            .select("id, user_id, payload, created_at, updated_at")
            .order("created_at", desc=True)
            .limit(min(limit, 10000))
            .execute()
        )

    resp = supabase_execute(_lire, description="liste toutes archives")
    return [_payload_to_archive(r) for r in (resp.data or [])]
