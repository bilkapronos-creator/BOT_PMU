#!/usr/bin/env python3
"""Vérifie la fraîcheur et le volume de api_velora_matchs.json (prod ou local)."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")
DEFAULT_URL = "https://velora-pronos.com/api_velora_matchs.json"
MATCH_FINISHED_GRACE_SEC = 120 * 60


def load_payload(source: str) -> dict | list:
    if source.startswith("http://") or source.startswith("https://"):
        req = urllib.request.Request(
            source,
            headers={"User-Agent": "Velora-Healthcheck/1.0", "Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode("utf-8"))
    path = Path(source)
    return json.loads(path.read_text(encoding="utf-8"))


def match_start_ts(m: dict) -> float | None:
    raw = m.get("match_start_ts")
    if raw is None:
        return None
    try:
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        return ts
    except (TypeError, ValueError):
        return None


def count_a_venir(matchs: list) -> int:
    now = time.time()
    n = 0
    for m in matchs:
        if not isinstance(m, dict):
            continue
        status = str(m.get("match_status") or "").strip().upper()
        if status in {"FINISHED", "ENDED", "CLOSED", "CANCELLED", "CANCELED"}:
            continue
        ts = match_start_ts(m)
        if ts is None:
            n += 1
            continue
        if ts >= now - MATCH_FINISHED_GRACE_SEC:
            n += 1
    return n


def meta_age_hours(payload: dict | list) -> float | None:
    if not isinstance(payload, dict):
        return None
    raw = (payload.get("meta") or {}).get("generated_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return (time.time() - dt.timestamp()) / 3600.0
    except (TypeError, ValueError):
        return None


def main() -> int:
    source = os.environ.get("VELORA_FOOT_JSON_URL", DEFAULT_URL).strip() or DEFAULT_URL
    min_matchs = int(os.environ.get("VELORA_CI_FOOT_MIN_MATCHS", "80"))
    max_age_h = float(os.environ.get("VELORA_CI_FOOT_MAX_AGE_H", "3"))

    try:
        payload = load_payload(source)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"::error::Impossible de lire {source} — {exc}")
        return 1

    matchs = payload if isinstance(payload, list) else payload.get("matchs") or []
    total = len(matchs)
    a_venir = count_a_venir(matchs)
    age_h = meta_age_hours(payload)
    gen = (payload.get("meta") or {}).get("generated_at") if isinstance(payload, dict) else None

    print(f"[healthcheck] source={source}")
    print(f"[healthcheck] total={total} a_venir={a_venir} min_requis={min_matchs}")
    if gen:
        print(f"[healthcheck] generated_at={gen} age_h={age_h:.1f}" if age_h is not None else f"[healthcheck] generated_at={gen}")

    failed = False
    if a_venir < min_matchs:
        print(
            f"::error::Base foot trop petite ({a_venir} matchs à venir < {min_matchs}). "
            "Scrape Winamax incomplet ou déploiement bloqué.",
        )
        failed = True
    if age_h is not None and age_h > max_age_h:
        print(
            f"::error::JSON foot périmé ({age_h:.1f} h > {max_age_h} h). "
            "Vérifier le cron GitHub et VELORA_PROXY_URL.",
        )
        failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
