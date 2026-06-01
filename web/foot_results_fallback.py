"""
Repli TheSportsDB (API gratuite) avec fuzzy matching des noms d'équipes.
"""
from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from foot_team_fuzzy import normalize_team, teams_pair_match

TZ_PARIS = ZoneInfo("Europe/Paris")
UA = (
    "Mozilla/5.0 (compatible; VeloraFootResolver/1.0; +https://velora.local)"
)
EVENTS_DAY_URL = "https://www.thesportsdb.com/api/v1/json/3/eventsday.php?d={date}&s=Soccer"
FUZZY_THRESHOLD = float(__import__("os").environ.get("VELORA_FUZZY_THRESHOLD", "0.68"))


def _http_get_json(url: str, timeout: int = 22) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _dates_a_tester(kickoff: datetime) -> list[str]:
    """Jour du match ± 1 (fuseaux / reports)."""
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=TZ_PARIS)
    base = kickoff.astimezone(TZ_PARIS).date()
    out: list[str] = []
    for delta in (0, -1, 1):
        d = base + timedelta(days=delta)
        s = d.strftime("%Y-%m-%d")
        if s not in out:
            out.append(s)
    return out


def _events_du_jour(date_str: str) -> list[dict]:
    try:
        data = _http_get_json(EVENTS_DAY_URL.format(date=date_str))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"[foot-fallback] TheSportsDB {date_str} ignoré : {exc}")
        return []
    events = (data or {}).get("events") or []
    return [e for e in events if isinstance(e, dict)]


def _score_depuis_event(ev: dict) -> dict[str, int] | None:
    try:
        dom = int(ev.get("intHomeScore"))
        ext = int(ev.get("intAwayScore"))
    except (TypeError, ValueError):
        return None
    if dom < 0 or ext < 0 or dom > 25 or ext > 25:
        return None
    status = str(ev.get("strStatus") or "").lower()
    if status and status not in (
        "match finished",
        "finished",
        "ft",
        "full time",
        "after penalties",
        "after extra time",
        "aet",
        "ap",
    ):
        if ev.get("intHomeScore") in (None, "") or ev.get("intAwayScore") in (None, ""):
            return None
    return {"domicile": dom, "exterieur": ext}


def _meilleur_event_fuzzy(
    events: list[dict],
    equipe_domicile: str,
    equipe_exterieur: str,
) -> tuple[dict | None, float]:
    meilleur: dict | None = None
    meilleur_score = 0.0
    for ev in events:
        home = str(ev.get("strHomeTeam") or "")
        away = str(ev.get("strAwayTeam") or "")
        ok, conf = teams_pair_match(
            equipe_domicile,
            equipe_exterieur,
            home,
            away,
            threshold=FUZZY_THRESHOLD,
        )
        if not ok:
            continue
        score_ev = _score_depuis_event(ev)
        if score_ev is None:
            continue
        if conf > meilleur_score:
            meilleur_score = conf
            meilleur = ev
    return meilleur, meilleur_score


def fetch_score_thesportsdb(
    equipe_domicile: str,
    equipe_exterieur: str,
    kickoff: datetime | None = None,
) -> dict[str, int] | None:
    """
    Cherche le score via TheSportsDB (eventsday) + fuzzy matching.
    Retourne { domicile, exterieur } ou None.
    """
    kickoff = kickoff or datetime.now(tz=TZ_PARIS)
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=TZ_PARIS)

    tous_events: list[dict] = []
    for date_str in _dates_a_tester(kickoff):
        tous_events.extend(_events_du_jour(date_str))

    if not tous_events:
        return None

    ev, conf = _meilleur_event_fuzzy(tous_events, equipe_domicile, equipe_exterieur)
    if not ev:
        print(
            f"[foot-fallback] TheSportsDB : aucune correspondance fuzzy "
            f"({normalize_team(equipe_domicile)} vs {normalize_team(equipe_exterieur)})"
        )
        return None

    score = _score_depuis_event(ev)
    if score:
        print(
            f"[foot-fallback] TheSportsDB (fuzzy {conf:.2f}) : "
            f"{equipe_domicile} {score['domicile']}-{score['exterieur']} {equipe_exterieur} "
            f"← {ev.get('strHomeTeam')} / {ev.get('strAwayTeam')}"
        )
    return score
