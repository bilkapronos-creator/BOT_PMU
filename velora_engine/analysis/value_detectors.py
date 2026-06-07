"""
Détection Value Bets FREE — Velora Engine v2 (B3).

Règle badges : uniquement marchés secondaires (pas 1N2 / DC) avec value détectée.
"""

from __future__ import annotations

from dataclasses import dataclass

from velora_engine.analysis.probability import edge_score, stars_from_edge
from velora_engine.config import EDGE_THRESHOLDS
from velora_engine.models import (
    MarketsRaw,
    OuLine,
    PrimaryPick,
    TeamGoalsSide,
    ValueBet,
)

VALUE_PREFIX = "🔥 Value Bet —"
FREE_PRIMARY_ORDER = (
    "1n2",
    "dc_1x",
    "dc_x2",
    "ou_total",
    "btts",
    "team_goals_home",
    "team_goals_away",
)
BADGE_MARKETS = frozenset({"ou_total", "btts", "team_goals_home", "team_goals_away"})


@dataclass
class FreeValueResult:
    value_bets: list[ValueBet]
    primary_pick: PrimaryPick | None
    display_badges: list[str]


def _threshold(market: str) -> float:
    return EDGE_THRESHOLDS.get(market, 1.08)


def _make_vb(
    market: str,
    pick: str,
    label: str,
    cote: float | None,
    edge: float,
    *,
    line: str | None = None,
    side: str | None = None,
    is_primary: bool = False,
) -> ValueBet:
    return ValueBet(
        market=market,
        pick=pick,
        label=label,
        cote=cote,
        edge=round(edge, 3),
        stars=stars_from_edge(edge),
        is_primary=is_primary,
        line=line,
        side=side,
    )


def _favorite_1n2(cotes: dict[str, float | None]) -> tuple[str | None, float | None]:
    best_k: str | None = None
    best_c: float | None = None
    for key in ("1", "N", "2"):
        c = cotes.get(key)
        if c is None or c <= 1.0:
            continue
        if best_c is None or c < best_c:
            best_c = float(c)
            best_k = key
    return best_k, best_c


def _outsider_value_trop_agressif(
    pick: str,
    cote: float | None,
    cotes: dict[str, float | None],
) -> bool:
    """Évite Irlande @ 15 quand la France est à 1.11."""
    fav_side, fav_cote = _favorite_1n2(cotes)
    if not fav_side or fav_cote is None or pick == fav_side:
        return False
    if fav_cote >= 1.45:
        return False
    try:
        c = float(cote) if cote is not None else 0.0
    except (TypeError, ValueError):
        return False
    return c >= 5.0


def detect_value_1n2(
    cotes: dict[str, float | None],
    probs: dict[str, int],
    *,
    home: str = "",
    away: str = "",
) -> list[ValueBet]:
    hits: list[tuple[float, ValueBet]] = []
    labels = {
        "1": home.strip() or "Dom",
        "N": "Nul",
        "2": away.strip() or "Ext",
    }
    for key in ("1", "N", "2"):
        c = cotes.get(key)
        p = probs.get(key, 0)
        e = edge_score(p, c)
        if e is None or e < _threshold("1n2"):
            continue
        if _outsider_value_trop_agressif(key, c, cotes):
            continue
        hits.append(
            (
                e,
                _make_vb(
                    "1n2",
                    key,
                    f"Victoire {labels[key]}" if key != "N" else "Match nul",
                    c,
                    e,
                ),
            )
        )

    p1 = probs.get("1", 0)
    p2 = probs.get("2", 0)
    pn = probs.get("N", 0)
    fav_prob = max(p1, p2)
    gap_fav_nul = fav_prob - pn
    if 0 <= gap_fav_nul < 10:
        fav_side = "1" if p1 >= p2 else "2"
        pick = "dc_1x" if fav_side == "1" else "dc_x2"
        label = "Double chance 1X" if pick == "dc_1x" else "Double chance X2"
        c = None
        e = edge_score(fav_prob, cotes.get(fav_side))
        if e and e >= _threshold("dc_1x"):
            hits.append(
                (
                    e,
                    _make_vb("dc_1x" if pick == "dc_1x" else "dc_x2", pick, label, c, e),
                )
            )

    hits.sort(key=lambda x: x[0], reverse=True)
    return [vb for _, vb in hits[:2]]


def detect_value_ou_total(markets: MarketsRaw) -> ValueBet | None:
    best: tuple[float, ValueBet] | None = None
    for line, ou in markets.over_under_total.items():
        for side, prob_key, cote_key, side_label in (
            ("plus", "plus_prob", "plus_cote", "Plus"),
            ("moins", "moins_prob", "moins_cote", "Moins"),
        ):
            prob = getattr(ou, prob_key, None)
            cote = getattr(ou, cote_key, None)
            e = edge_score(prob, cote)
            if e is None or e < _threshold("ou_total"):
                continue
            lbl = f"{side_label} de {line} buts"
            vb = _make_vb("ou_total", side, lbl, cote, e, line=line, side=side)
            if best is None or e > best[0]:
                best = (e, vb)
    return best[1] if best else None


def detect_value_btts(markets: MarketsRaw, les_deux_marquent: int | None) -> ValueBet | None:
    btts = markets.btts
    if not btts:
        return None
    best: tuple[float, ValueBet] | None = None
    for side in ("oui", "non"):
        cote = btts.get(side)
        prob = les_deux_marquent if side == "oui" else (100 - les_deux_marquent if les_deux_marquent is not None else None)
        e = edge_score(prob, cote)
        if e is None or e < _threshold("btts"):
            continue
        lbl = f"BTTS : {'OUI' if side == 'oui' else 'NON'}"
        vb = _make_vb("btts", side, lbl, cote, e)
        if best is None or e > best[0]:
            best = (e, vb)
    return best[1] if best else None


def _detect_team_goals_side(
    side_key: str,
    market_key: str,
    tg: TeamGoalsSide,
) -> ValueBet | None:
    best: tuple[float, ValueBet] | None = None
    for line, ou in tg.lines.items():
        for pick_side, prob_key, cote_key, fr in (
            ("plus", "plus_prob", "plus_cote", "marque +"),
            ("moins", "moins_prob", "moins_cote", "marque -"),
        ):
            prob = getattr(ou, prob_key, None)
            cote = getattr(ou, cote_key, None)
            e = edge_score(prob, cote)
            if e is None or e < _threshold(market_key):
                continue
            lbl = f"{tg.team_name} {fr}{line} buts"
            vb = _make_vb(market_key, pick_side, lbl, cote, e, line=line, side=pick_side)
            if best is None or e > best[0]:
                best = (e, vb)
    return best[1] if best else None


def detect_all_free_values(
    *,
    cotes_1n2: dict[str, float | None],
    probs: dict[str, int],
    markets: MarketsRaw,
    les_deux_marquent: int | None = None,
    home: str = "",
    away: str = "",
) -> FreeValueResult:
    pool: list[ValueBet] = []
    pool.extend(detect_value_1n2(cotes_1n2, probs, home=home, away=away))

    ou_vb = detect_value_ou_total(markets)
    if ou_vb:
        pool.append(ou_vb)

    btts_vb = detect_value_btts(markets, les_deux_marquent)
    if btts_vb:
        pool.append(btts_vb)

    if "home" in markets.team_goals:
        tg = _detect_team_goals_side("home", "team_goals_home", markets.team_goals["home"])
        if tg:
            pool.append(tg)
    if "away" in markets.team_goals:
        tg = _detect_team_goals_side("away", "team_goals_away", markets.team_goals["away"])
        if tg:
            pool.append(tg)

    primary_vb: ValueBet | None = None
    for market_id in FREE_PRIMARY_ORDER:
        candidates = [v for v in pool if v.market == market_id]
        if candidates:
            primary_vb = max(candidates, key=lambda v: v.edge)
            break
    if primary_vb is None and pool:
        primary_vb = max(pool, key=lambda v: v.edge)

    if primary_vb:
        for v in pool:
            v.is_primary = v.market == primary_vb.market and v.pick == primary_vb.pick

    badges: list[str] = []
    for v in pool:
        if v.market not in BADGE_MARKETS:
            continue
        if v.label not in badges:
            badges.append(v.label)

    primary_pick = None
    if primary_vb:
        conseil = f"{VALUE_PREFIX} {primary_vb.label}"
        if primary_vb.cote:
            conseil = f"{VALUE_PREFIX} {primary_vb.label} @ {primary_vb.cote:.2f}"
        primary_pick = PrimaryPick(
            market=primary_vb.market,
            pick=primary_vb.pick,
            label=primary_vb.label,
            cote=primary_vb.cote,
            conseil_short=conseil,
        )

    return FreeValueResult(
        value_bets=pool,
        primary_pick=primary_pick,
        display_badges=badges,
    )
