"""
Récupération automatique des scores finaux Foot via Winamax (PRELOADED_STATE).
Utilisé par velora_archiver_foot.py (projet BOT_PMU).
"""
from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from parser_winamax import (
    FOOTBALL_SPORT_ID,
    MATCH_FINISHED_GRACE_MINUTES,
    MATCH_LIVE_STATUSES,
    is_winamax_match_finished,
)

MATCH_SKIP_STATUSES = frozenset(
    {
        "LIVE",
        "RUNNING",
        "INPLAY",
        "IN_PLAY",
        "PREMATCH",
        "NOT_STARTED",
        "POSTPONED",
        "CANCELLED",
        "CANCELED",
        "DELAYED",
        "SUSPENDED",
        "ABANDONED",
    }
)
from winamax_dump import _extract_via_regex

TZ_PARIS = ZoneInfo("Europe/Paris")
MATCH_URL = "https://www.winamax.fr/paris-sportifs/match/{match_id}"
BULK_URLS = (
    "https://www.winamax.fr/paris-sportifs/sports/1",
    "https://www.winamax.fr/paris-sportifs/live",
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": "https://www.winamax.fr/paris-sportifs/sports/1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}
REQUEST_PAUSE_SEC = float(os.environ.get("WINAMAX_RESULTS_PAUSE", "0.35"))
REQUEST_TIMEOUT = int(os.environ.get("WINAMAX_RESULTS_TIMEOUT", "28"))


def parse_winamax_score_label(label: Any) -> tuple[int, int] | None:
    """Convertit '2:1', '2 - 1', '1:1 - 1:1' (score final) en (dom, ext)."""
    if label is None:
        return None
    text = str(label).strip().replace("–", "-")
    if not text:
        return None
    if " - " in text:
        parts = [p.strip() for p in text.split(" - ") if p.strip()]
        if parts:
            text = parts[-1]
    sep = ":" if ":" in text else ("-" if "-" in text else None)
    if sep:
        bits = [b.strip() for b in text.split(sep, 1)]
        if len(bits) == 2 and bits[0].isdigit() and bits[1].isdigit():
            return int(bits[0]), int(bits[1])
    nums = [int(x) for x in re.findall(r"\d+", text)]
    if len(nums) >= 2:
        return nums[0], nums[1]
    return None


def est_match_winamax_a_ignorer(raw_match: dict) -> bool:
    """True si live / reporté / annulé — on retentera au prochain run."""
    if not isinstance(raw_match, dict):
        return True
    status = str(raw_match.get("status") or "").strip().upper().replace(" ", "_")
    return status in MATCH_SKIP_STATUSES or status in MATCH_LIVE_STATUSES


def extraire_score_final_match(raw_match: dict) -> dict[str, int] | None:
    """Score final domicile / extérieur si le match Winamax est terminé (matchStatus 3 / ENDED)."""
    if not isinstance(raw_match, dict):
        return None
    sid = raw_match.get("sportId")
    if sid is not None and sid != FOOTBALL_SPORT_ID:
        return None

    if est_match_winamax_a_ignorer(raw_match):
        return None

    kickoff_ts = None
    raw_start = raw_match.get("matchStart") or raw_match.get("matchStartDate")
    if raw_start is not None:
        try:
            kickoff_ts = float(raw_start)
            if kickoff_ts > 1e12:
                kickoff_ts /= 1000.0
        except (TypeError, ValueError):
            kickoff_ts = None

    if not is_winamax_match_finished(raw_match, int(kickoff_ts) if kickoff_ts else None):
        return None

    for cle in ("score", "setScores", "finalScore", "result"):
        tpl = parse_winamax_score_label(raw_match.get(cle))
        if tpl:
            dom, ext = tpl
            return {"domicile": dom, "exterieur": ext}
    return None


def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _fetch_preloaded_matches() -> dict[str, dict]:
    """Charge l'état Winamax (pages sports + live) indexé par id match."""
    merged: dict[str, dict] = {}
    for url in BULK_URLS:
        try:
            html = _http_get(url)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"[winamax-results] Bulk ignoré ({url}) : {exc}")
            continue
        data, _src = _extract_via_regex(html)
        if not isinstance(data, dict):
            continue
        for mid, m in (data.get("matches") or {}).items():
            if isinstance(m, dict):
                merged[str(mid)] = m
        time.sleep(REQUEST_PAUSE_SEC)
    return merged


def _fetch_match_raw(match_id: str) -> dict | None:
    url = MATCH_URL.format(match_id=match_id)
    try:
        html = _http_get(url)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[winamax-results] Match {match_id} : {exc}")
        return None
    data, _ = _extract_via_regex(html)
    if not isinstance(data, dict):
        return None
    matches = data.get("matches") or {}
    for key in (match_id, str(match_id)):
        m = matches.get(key)
        if isinstance(m, dict):
            return m
        try:
            m = matches.get(int(match_id))
            if isinstance(m, dict):
                return m
        except (TypeError, ValueError):
            pass
    return None


def fetch_winamax_results(match_ids: list[str] | set[str]) -> dict[str, dict[str, int]]:
    """
    Interroge Winamax pour les id_match demandés.
    Retourne { id_match: { domicile, exterieur } } (uniquement scores disponibles).
    Les matchs sans score final ne figurent pas dans le dict (fallback : prochain run).
    """
    ids = [str(i).strip() for i in match_ids if str(i).strip()]
    if not ids:
        return {}

    bulk = _fetch_preloaded_matches()
    out: dict[str, dict[str, int]] = {}
    manquants: list[str] = []

    for mid in ids:
        raw = bulk.get(mid)
        score = extraire_score_final_match(raw) if raw else None
        if score:
            out[mid] = score
        else:
            manquants.append(mid)

    for mid in manquants:
        time.sleep(REQUEST_PAUSE_SEC)
        try:
            raw = _fetch_match_raw(mid)
            if not raw:
                continue
            score = extraire_score_final_match(raw)
            if score:
                out[mid] = score
        except Exception as exc:
            print(f"[winamax-results] Ignoré {mid} : {exc}")
            continue

    return out


def match_devrait_avoir_score(
    match: dict,
    now: datetime | None = None,
    veille_uniquement: bool = False,
) -> bool:
    """True si le coup d'envoi est passé (grace 2 h) — option veille = jour précédent (Paris)."""
    now = now or datetime.now(tz=TZ_PARIS)
    ts = match.get("match_start_ts")
    kickoff: datetime | None = None
    if ts is not None:
        try:
            t = float(ts)
            if t > 1e12:
                t /= 1000.0
            kickoff = datetime.fromtimestamp(t, tz=TZ_PARIS)
        except (TypeError, ValueError, OSError, OverflowError):
            kickoff = None

    if kickoff is None:
        return False

    if veille_uniquement:
        hier = (now.date() - timedelta(days=1))
        if kickoff.date() != hier:
            return False

    grace = kickoff + timedelta(minutes=MATCH_FINISHED_GRACE_MINUTES)
    return now >= grace


def fusionner_resultats_fichier(
    path: Path,
    nouveaux: dict[str, dict[str, int]],
) -> dict[str, Any]:
    """Met à jour velora_foot_resultats.json (clé matchs + compat racine)."""
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}

    if not isinstance(data, dict):
        data = {}

    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    meta.setdefault(
        "description",
        "Scores finaux par id_match Winamax — alimente velora_archiver_foot.py",
    )
    meta["format"] = 'id_match: { "domicile": X, "exterieur": Y }'
    meta["derniere_sync"] = datetime.now(tz=TZ_PARIS).isoformat()
    data["meta"] = meta

    matchs = data.get("matchs")
    if not isinstance(matchs, dict):
        matchs = {}
        for k, v in list(data.items()):
            if str(k) in ("meta", "matchs"):
                continue
            if isinstance(v, dict) and ("domicile" in v or "exterieur" in v):
                matchs[str(k)] = v
    for mid, score in nouveaux.items():
        matchs[str(mid)] = {
            "domicile": int(score["domicile"]),
            "exterieur": int(score["exterieur"]),
        }
    data["matchs"] = matchs

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def main() -> int:
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ids = [a for a in sys.argv[1:] if a.strip()]
    if not ids:
        print("Usage: python winamax_foot_results.py <id_match> [id_match ...]")
        return 1
    scores = fetch_winamax_results(ids)
    for mid, sc in scores.items():
        print(f"{mid} -> {sc['domicile']}-{sc['exterieur']}")
    print(f"[winamax-results] {len(scores)}/{len(ids)} score(s) récupéré(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
