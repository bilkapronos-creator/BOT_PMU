"""Constantes et helpers partagés (évite imports circulaires database ↔ supabase/sqlite)."""

import os
from pathlib import Path

DB_PATH = Path(os.environ.get("VELORA_DB_PATH", "velora_engine.db"))
ARCHIVES_MAX_PER_USER = int(os.environ.get("ARCHIVES_MAX_PER_USER", "500"))


def course_key(archive: dict) -> str:
    date_api = archive.get("dateApi") or archive.get("date_api") or ""
    reunion = archive.get("reunion") or ""
    course = archive.get("course") or ""
    return f"{date_api}-{reunion}-{course}"
