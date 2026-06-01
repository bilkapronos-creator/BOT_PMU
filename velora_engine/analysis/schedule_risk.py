"""
Heuristique calendrier Winamax (dump) — matchs pièges / rotations.

Croise les matchs d'une même équipe dans la fenêtre du scraper sans API externe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from velora_engine.models import ProAlert
from velora_engine.scrape.markets_extractor import extract_competition_meta

TZ_PARIS = ZoneInfo("Europe/Paris")
FOOTBALL_SPORT_ID = 1

FAVORITE_ODD_MAX = 1.70
ROTATION_DAYS_MIN = 3
ROTATION_DAYS_MAX = 5
HIGH_STAKES = frozenset({"high"})


@dataclass
class TeamFixture:
    match_id: str
    team: str
    side: str  # home | away
    opponent: str
    start_ts: int
    stakes_tier: str
    competition_type: str
    competition_name: str


def _parse_start_ts(raw_match: dict) -> int | None:
    raw = raw_match.get("matchStart") or raw_match.get("matchStartDate")
    if raw is None:
        return None
    try:
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        return int(ts)
    except (TypeError, ValueError):
        return None


def _get_teams(raw_match: dict) -> tuple[str, str]:
    home = raw_match.get("competitor1Name")
    away = raw_match.get("competitor2Name")
    if home and away:
        return str(home).strip(), str(away).strip()
    title = str(raw_match.get("title") or "")
    for sep in (" - ", " – ", " vs "):
        if sep in title:
            a, b = title.split(sep, 1)
            return a.strip(), b.strip()
    return "?", "?"


def build_schedule_index(state: dict | None) -> dict[str, list[TeamFixture]]:
    """Index équipe normalisée (lower) → liste de matchs à venir dans le dump."""
    index: dict[str, list[TeamFixture]] = {}
    if not state or not isinstance(state, dict):
        return index
    matches = state.get("matches") or {}
    if not isinstance(matches, dict):
        return index

    for match_id, raw in matches.items():
        if not isinstance(raw, dict) or raw.get("sportId") != FOOTBALL_SPORT_ID:
            continue
        home, away = _get_teams(raw)
        if home == "?" or away == "?":
            continue
        start_ts = _parse_start_ts(raw)
        if start_ts is None:
            continue
        comp = extract_competition_meta(raw, state)
        mid = str(raw.get("matchId") or match_id)
        for side, team, opp in (
            ("home", home, away),
            ("away", away, home),
        ):
            key = team.lower().strip()
            index.setdefault(key, []).append(
                TeamFixture(
                    match_id=mid,
                    team=team,
                    side=side,
                    opponent=opp,
                    start_ts=start_ts,
                    stakes_tier=comp.stakes_tier,
                    competition_type=comp.type,
                    competition_name=comp.name,
                )
            )
    for fixtures in index.values():
        fixtures.sort(key=lambda f: f.start_ts)
    return index


def _favorite_side(cotes: dict[str, float | None]) -> tuple[str | None, float | None]:
    best_key = None
    best_odd = None
    for key in ("1", "2"):
        c = cotes.get(key)
        if c is None:
            continue
        try:
            cf = float(c)
        except (TypeError, ValueError):
            continue
        if best_odd is None or cf < best_odd:
            best_odd = cf
            best_key = key
    return best_key, best_odd


def check_upcoming_schedule_risk(
    *,
    match_id: str,
    home: str,
    away: str,
    match_start_ts: int | None,
    cotes_1n2: dict[str, float | None],
    current_competition,
    schedule_index: dict[str, list[TeamFixture]],
) -> ProAlert | None:
    """
    Alerte si le favori a un gros match (stakes high) dans 3–5 jours
    et le match actuel n'est pas à enjeu équivalent.
    """
    if match_start_ts is None:
        return None
    fav_side, fav_odd = _favorite_side(cotes_1n2)
    if not fav_side or fav_odd is None or fav_odd > FAVORITE_ODD_MAX:
        return None

    fav_team = home if fav_side == "1" else away
    fav_key = fav_team.lower().strip()
    fixtures = schedule_index.get(fav_key) or []
    if not fixtures:
        return None

    now_ts = match_start_ts
    window_end = now_ts + int(timedelta(days=ROTATION_DAYS_MAX).total_seconds())
    window_start = now_ts + int(timedelta(days=ROTATION_DAYS_MIN).total_seconds())

    current_high = current_competition.stakes_tier in HIGH_STAKES
    if current_high:
        return None

    for fx in fixtures:
        if fx.match_id == str(match_id):
            continue
        if fx.start_ts <= now_ts:
            continue
        if fx.start_ts < window_start or fx.start_ts > window_end:
            continue
        if fx.stakes_tier not in HIGH_STAKES:
            continue
        days_ahead = max(1, round((fx.start_ts - now_ts) / 86400))
        suggested = (
            {"market": "1n2", "pick": "dc_x2"}
            if fav_side == "1"
            else {"market": "1n2", "pick": "dc_1x"}
        )
        return ProAlert(
            type="rotation_risk",
            severity="high",
            team=fav_team,
            message=(
                f"{fx.competition_name} dans {days_ahead} j — "
                f"risque de rotation sur {fav_team}"
            ),
            suggested_pick=suggested,
        )
    return None
