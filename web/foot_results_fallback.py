"""
Repli gratuit pour scores foot (TheSportsDB) quand Winamax ne renvoie pas le score final.
"""
from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

TZ_PARIS = ZoneInfo("Europe/Paris")
UA = (
    "Mozilla/5.0 (compatible; VeloraFootResolver/1.0; +https://velora.local)"
)
EVENTS_DAY_URL = "https://www.thesportsdb.com/api/v1/json/3/eventsday.php?d={date}&s=Soccer"


def _norm_team(name: str) -> str:
    s = str(name or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    for tok in ("fc", "cf", "sc", "ac", "as", "us", "ud", "cd", "rc", "real", "de", "la", "le", "les"):
        s = re.sub(rf"\b{tok}\b", " ", s)
    return " ".join(s.split())


def _teams_match(a: str, b: str, c: str, d: str) -> bool:
    na, nb, nc, nd = _norm_team(a), _norm_team(b), _norm_team(c), _norm_team(d)
    if not na or not nb or not nc or not nd:
        return False
    direct = (na in nc or nc in na) and (nb in nd or nd in nb)
    croise = (na in nd or nd in na) and (nb in nc or nc in nb)
    return direct or croise


def _http_get_json(url: str, timeout: int = 22) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def fetch_score_thesportsdb(
    equipe_domicile: str,
    equipe_exterieur: str,
    kickoff: datetime | None = None,
) -> dict[str, int] | None:
    """
    Cherche le score du jour (Soccer) par noms d'équipes.
    Retourne { domicile, exterieur } ou None.
    """
    kickoff = kickoff or datetime.now(tz=TZ_PARIS)
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=TZ_PARIS)
    date_str = kickoff.astimezone(TZ_PARIS).strftime("%Y-%m-%d")
    url = EVENTS_DAY_URL.format(date=date_str)
    try:
        data = _http_get_json(url)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"[foot-fallback] TheSportsDB ignoré ({date_str}) : {exc}")
        return None

    events = (data or {}).get("events") or []
    if not isinstance(events, list):
        return None

    for ev in events:
        if not isinstance(ev, dict):
            continue
        home = str(ev.get("strHomeTeam") or "")
        away = str(ev.get("strAwayTeam") or "")
        if not _teams_match(equipe_domicile, equipe_exterieur, home, away):
            continue
        status = str(ev.get("strStatus") or "").lower()
        if status and status not in ("match finished", "finished", "ft", "full time"):
            hs = ev.get("intHomeScore")
            aws = ev.get("intAwayScore")
            if hs in (None, "") or aws in (None, ""):
                continue
        try:
            dom = int(ev.get("intHomeScore"))
            ext = int(ev.get("intAwayScore"))
        except (TypeError, ValueError):
            continue
        print(
            f"[foot-fallback] Score TheSportsDB : {equipe_domicile} {dom}-{ext} {equipe_exterieur}"
        )
        return {"domicile": dom, "exterieur": ext}
    return None
