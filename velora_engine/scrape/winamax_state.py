"""Utilitaires lecture PRELOADED_STATE Winamax (partagés scraper / extractor)."""

from __future__ import annotations

import re
from typing import Any

OU_MAX_COTE_SANE = 5.0


def lookup(mapping: dict, key: Any) -> dict | None:
    try:
        val = mapping.get(str(key)) or mapping.get(key)
        return val if isinstance(val, dict) else None
    except Exception:
        return None


def lookup_odd(odds: dict | None, outcome_id: Any) -> float | None:
    if not odds:
        return None
    try:
        val = odds.get(str(outcome_id)) or odds.get(outcome_id)
        if isinstance(val, dict):
            val = val.get("odds") or val.get("price")
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def bets_for_match(bets: dict, match_id: Any) -> list[dict]:
    out: list[dict] = []
    if not isinstance(bets, dict):
        return out
    mid = str(match_id)
    for bet in bets.values():
        if not isinstance(bet, dict):
            continue
        if str(bet.get("matchId")) == mid:
            out.append(bet)
    return out


def safe_float(val: Any) -> float | None:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def bet_label(bet: dict) -> str:
    parts = [
        bet.get("betTypeName"),
        bet.get("betTitle"),
        bet.get("betFilterName"),
        bet.get("specialBetValue"),
    ]
    return " ".join(str(p) for p in parts if p).strip().lower()


def outcome_pct(out: dict) -> int | None:
    try:
        prob = out.get("percentDistribution") or out.get("probability")
        if prob is None:
            return None
        p = float(prob)
        if 0 < p <= 1:
            return int(round(p * 100))
        return int(round(p))
    except (TypeError, ValueError):
        return None


def sanitize_ou_cote(price: float | None, max_cote: float = OU_MAX_COTE_SANE) -> float | None:
    if price is None:
        return None
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p > max_cote or p < 1.01:
        return None
    return round(p, 2)


def parse_ou_side_line(label: str) -> tuple[str | None, str | None]:
    """Retourne (plus|moins, ligne) — lignes dynamiques ex. 0.5, 1.5, 2.5, 3.5."""
    try:
        lab = str(label or "").lower().replace(",", ".")
        m = re.search(r"(plus|moins)\s*(?:de\s*)?(\d+(?:\.\d+)?)", lab)
        if not m:
            return None, None
        side = m.group(1)
        line = m.group(2)
        if side in ("plus", "moins") and line:
            return side, line
    except Exception:
        pass
    return None, None


def find_raw_match(state: dict | None, match_id: Any) -> dict | None:
    if not state or not isinstance(state, dict):
        return None
    matches = state.get("matches") or {}
    if not isinstance(matches, dict):
        return None
    key = str(match_id)
    if key in matches and isinstance(matches[key], dict):
        return matches[key]
    try:
        kid = int(match_id)
        if kid in matches and isinstance(matches[kid], dict):
            return matches[kid]
    except (TypeError, ValueError):
        pass
    for m in matches.values():
        if isinstance(m, dict) and str(m.get("matchId")) == key:
            return m
    return None


def resolve_category_name(state: dict, category_id: Any) -> str | None:
    cats = state.get("categories") or state.get("category") or {}
    if not isinstance(cats, dict):
        return None
    row = lookup(cats, category_id)
    if not row:
        return None
    for key in ("categoryName", "name", "label", "title"):
        val = row.get(key)
        if val:
            return str(val).strip()
    return None


def resolve_tournament_name(state: dict, tournament_id: Any) -> str | None:
    tours = state.get("tournaments") or state.get("tournament") or {}
    if not isinstance(tours, dict):
        return None
    row = lookup(tours, tournament_id)
    if not row:
        return None
    for key in ("tournamentName", "name", "label", "title"):
        val = row.get(key)
        if val:
            return str(val).strip()
    return None
