"""
Conseils intelligents — scanne tous les marchés Winamax disponibles et classe
les paris par intérêt réel (probabilité × cote), pas seulement par proba élevée.

Une cote < 1.50 n'est pas avantageuse sauf edge exceptionnel (ex. 1N2 @ 1.20
avec 90% de réussite modèle ne vaut pas un Over @ 2.10 avec 58%).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from velora_engine.analysis.probability import edge_score, stars_from_edge
from velora_engine.config import (
    ADVISOR_TOP_N,
    EDGE_THRESHOLDS,
    LOW_ODDS_EDGE_MIN,
    MIN_COTE_AVANTAGEUSE,
)
from velora_engine.models import (
    BetRecommendation,
    MarketsRaw,
    OuLine,
    PremiumAnalysis,
    ValueBet,
)

MARKET_LABELS: dict[str, str] = {
    "1n2": "1N2",
    "dc_1x": "Double chance 1X",
    "dc_x2": "Double chance X2",
    "dc_12": "Double chance 12",
    "dnb": "Remboursé si nul",
    "ou_total": "Over/Under",
    "btts": "BTTS",
    "team_goals_home": "Buts domicile",
    "team_goals_away": "Buts extérieur",
    "half_time_1n2": "Mi-temps 1N2",
    "handicap": "Handicap",
    "exact_goals": "Nb buts exact",
    "score_exact": "Score exact",
    "buteur_match": "Buteur",
    "buteur_mi_temps": "Buteur MT",
    "buteur_double": "Buteur doublé",
}


@dataclass
class _Opp:
    market: str
    pick: str
    label: str
    cote: float | None
    prob_pct: int
    edge: float


def _threshold(market: str) -> float:
    return EDGE_THRESHOLDS.get(market, 1.08)


def _tier(edge: float, cote: float | None) -> str:
    c = float(cote or 0)
    if edge >= 1.12 and c >= MIN_COTE_AVANTAGEUSE:
        return "excellent"
    if c < MIN_COTE_AVANTAGEUSE:
        return "prudent"
    if edge >= 1.08:
        return "bon"
    return "value"


def _raison(prob_pct: int, cote: float | None, edge: float, tier: str) -> str:
    c = f"{cote:.2f}" if cote else "?"
    base = f"Proba {prob_pct}% × cote {c} → edge {edge:.2f}"
    if tier == "prudent":
        return f"{base} — cote faible (<{MIN_COTE_AVANTAGEUSE:.2f}), gain limité"
    if tier == "excellent":
        return f"{base} — bon équilibre proba/rendement"
    return base


def _advantage_score(prob_pct: int, cote: float | None, edge: float) -> float:
    """Score de classement : EV ajusté (pénalise les cotes trop basses)."""
    if cote is None or cote <= 1.0 or edge is None:
        return -1.0
    ev = edge - 1.0
    c = float(cote)
    if c < MIN_COTE_AVANTAGEUSE:
        if edge < LOW_ODDS_EDGE_MIN:
            return -1.0
        ev *= (c / MIN_COTE_AVANTAGEUSE) ** 1.4
    elif 1.6 <= c <= 4.5:
        ev *= 1.08
    elif c > 8.0:
        ev *= 0.92
    conf = 1.0 + min(0.25, prob_pct / 400.0)
    return ev * conf * (1.0 + math.log(c) * 0.15)


def _passes_filters(market: str, cote: float | None, edge: float | None) -> bool:
    if edge is None or cote is None or cote <= 1.01:
        return False
    if edge < _threshold(market):
        return False
    if cote < MIN_COTE_AVANTAGEUSE and edge < LOW_ODDS_EDGE_MIN:
        return False
    return True


def _add(
    pool: list[_Opp],
    *,
    market: str,
    pick: str,
    label: str,
    cote: float | None,
    prob_pct: int | None,
) -> None:
    if prob_pct is None or prob_pct <= 0:
        return
    e = edge_score(prob_pct, cote)
    if not _passes_filters(market, cote, e):
        return
    pool.append(
        _Opp(
            market=market,
            pick=pick,
            label=label,
            cote=cote,
            prob_pct=int(prob_pct),
            edge=round(float(e), 3),
        )
    )


def _dc_probs(probs: dict[str, int]) -> dict[str, int]:
    p1 = int(probs.get("1") or 0)
    pn = int(probs.get("N") or 0)
    p2 = int(probs.get("2") or 0)
    return {
        "1x": min(100, p1 + pn),
        "x2": min(100, p2 + pn),
        "12": min(100, p1 + p2),
    }


def _collect_1n2_and_dc(
    pool: list[_Opp],
    *,
    cotes_1n2: dict[str, float | None],
    probs: dict[str, int],
    home: str,
    away: str,
    markets: MarketsRaw,
) -> None:
    labels = {"1": home or "Dom", "N": "Nul", "2": away or "Ext"}
    for key in ("1", "N", "2"):
        _add(
            pool,
            market="1n2",
            pick=key,
            label=f"Victoire {labels[key]}" if key != "N" else "Match nul",
            cote=cotes_1n2.get(key),
            prob_pct=probs.get(key),
        )

    dc_model = _dc_probs(probs)
    dc_labels = {
        "1x": "Double chance 1X",
        "x2": "Double chance X2",
        "12": "Double chance 12",
    }
    dc_market_keys = {"1x": "dc_1x", "x2": "dc_x2", "12": "dc_12"}
    for pick, mo in (markets.double_chance or {}).items():
        pk = pick.lower()
        _add(
            pool,
            market=dc_market_keys.get(pk, "dc_1x"),
            pick=pk,
            label=dc_labels.get(pk, f"Double chance {pick.upper()}"),
            cote=mo.cote,
            prob_pct=mo.prob or dc_model.get(pk),
        )
    # Sans cotes Winamax « Double chance », ne pas estimer via 1N2 (ex. DC 12 @ cote victoire dom).


def _dedupe_dc_same_cote(pool: list[_Opp]) -> list[_Opp]:
    """Si plusieurs DC partagent la même cote bookmaker, ne garder que le meilleur score."""
    dc_rows: list[_Opp] = []
    other: list[_Opp] = []
    for opp in pool:
        if opp.market.startswith("dc_"):
            dc_rows.append(opp)
        else:
            other.append(opp)
    by_cote: dict[float, list[_Opp]] = {}
    for opp in dc_rows:
        if opp.cote is None:
            other.append(opp)
            continue
        key = round(float(opp.cote), 2)
        by_cote.setdefault(key, []).append(opp)
    kept_dc: list[_Opp] = []
    for group in by_cote.values():
        if len(group) == 1:
            kept_dc.append(group[0])
        else:
            kept_dc.append(
                max(group, key=lambda o: _advantage_score(o.prob_pct, o.cote, o.edge)),
            )
    return other + kept_dc


def _collect_dnb(
    pool: list[_Opp],
    *,
    probs: dict[str, int],
    home: str,
    away: str,
    markets: MarketsRaw,
) -> None:
    p1 = int(probs.get("1") or 0)
    p2 = int(probs.get("2") or 0)
    for side, mo in (markets.dnb or {}).items():
        prob = mo.prob or (p1 if side == "home" else p2)
        team = home if side == "home" else away
        _add(
            pool,
            market="dnb",
            pick=side,
            label=f"DNB {team or side}",
            cote=mo.cote,
            prob_pct=prob,
        )


def _collect_ou(
    pool: list[_Opp],
    *,
    markets: MarketsRaw,
    prob_over_25_modele: int | None,
) -> None:
    for line, ou in markets.over_under_total.items():
        for side, prob_key, cote_key, fr in (
            ("plus", "plus_prob", "plus_cote", "Plus"),
            ("moins", "moins_prob", "moins_cote", "Moins"),
        ):
            prob = getattr(ou, prob_key, None)
            cote = getattr(ou, cote_key, None)
            if prob is None and line == "2.5" and prob_over_25_modele is not None:
                prob = prob_over_25_modele if side == "plus" else 100 - prob_over_25_modele
            _add(
                pool,
                market="ou_total",
                pick=f"{side}_{line}",
                label=f"{fr} de {line} buts",
                cote=cote,
                prob_pct=prob,
            )


def _collect_btts(
    pool: list[_Opp],
    *,
    markets: MarketsRaw,
    les_deux_marquent: int | None,
    prob_btts_modele: int | None,
) -> None:
    btts = markets.btts
    if not btts:
        return
    for side in ("oui", "non"):
        cote = btts.get(side)
        if side == "oui":
            prob = les_deux_marquent or prob_btts_modele
        else:
            base = les_deux_marquent or prob_btts_modele
            prob = (100 - base) if base is not None else None
        _add(
            pool,
            market="btts",
            pick=side,
            label=f"BTTS {'Oui' if side == 'oui' else 'Non'}",
            cote=cote,
            prob_pct=prob,
        )


def _collect_team_goals(pool: list[_Opp], *, markets: MarketsRaw) -> None:
    for side_key, market_key in (("home", "team_goals_home"), ("away", "team_goals_away")):
        tg = markets.team_goals.get(side_key)
        if not tg:
            continue
        for line, ou in tg.lines.items():
            for pick_side, prob_key, cote_key, fr in (
                ("plus", "plus_prob", "plus_cote", "+"),
                ("moins", "moins_prob", "moins_cote", "-"),
            ):
                _add(
                    pool,
                    market=market_key,
                    pick=f"{pick_side}_{line}",
                    label=f"{tg.team_name} {fr}{line} buts",
                    cote=getattr(ou, cote_key, None),
                    prob_pct=getattr(ou, prob_key, None),
                )


def _collect_half_time(
    pool: list[_Opp],
    *,
    markets: MarketsRaw,
    probs: dict[str, int],
    home: str,
    away: str,
) -> None:
    ht = markets.half_time_1n2
    if not ht:
        return
    labels = {"1": home or "Dom", "N": "Nul MT", "2": away or "Ext"}
    for pick, mo in ht.items():
        prob = mo.prob
        if prob is None and pick in probs:
            prob = max(8, int(int(probs[pick]) * 0.55))
        _add(
            pool,
            market="half_time_1n2",
            pick=pick,
            label=f"MT — {labels.get(pick, pick)}",
            cote=mo.cote,
            prob_pct=prob,
        )


def _collect_handicap(
    pool: list[_Opp],
    *,
    markets: MarketsRaw,
    home: str,
    away: str,
) -> None:
    for line, sides in (markets.handicap or {}).items():
        for pick, mo in sides.items():
            team = home if pick == "1" else away if pick == "2" else "Nul"
            _add(
                pool,
                market="handicap",
                pick=f"{pick}_{line}",
                label=f"Handicap {line} — {team}",
                cote=mo.cote,
                prob_pct=mo.prob,
            )


def _collect_exact_goals(pool: list[_Opp], *, markets: MarketsRaw) -> None:
    for pick, mo in (markets.exact_goals or {}).items():
        _add(
            pool,
            market="exact_goals",
            pick=pick,
            label=f"Exactement {pick} but(s)",
            cote=mo.cote,
            prob_pct=mo.prob,
        )


def _collect_premium(
    pool: list[_Opp],
    *,
    premium: PremiumAnalysis | None,
) -> None:
    if not premium:
        return
    for vb in premium.value_bets or []:
        if vb.cote and vb.edge:
            pool.append(
                _Opp(
                    market=str(vb.market),
                    pick=str(vb.pick),
                    label=str(vb.label),
                    cote=vb.cote,
                    prob_pct=int(round(vb.edge / float(vb.cote) * 100)) if vb.cote else 0,
                    edge=vb.edge,
                )
            )


def _collect_value_bets(pool: list[_Opp], *, value_bets: list[ValueBet]) -> None:
    for vb in value_bets:
        if not vb.cote or not vb.edge:
            continue
        prob = int(round(vb.edge / float(vb.cote) * 100)) if vb.cote else 0
        pool.append(
            _Opp(
                market=str(vb.market),
                pick=str(vb.pick),
                label=str(vb.label),
                cote=vb.cote,
                prob_pct=prob,
                edge=vb.edge,
            )
        )


def _to_recommendation(opp: _Opp) -> BetRecommendation:
    tier = _tier(opp.edge, opp.cote)
    score = _advantage_score(opp.prob_pct, opp.cote, opp.edge)
    stars = stars_from_edge(opp.edge)
    if tier == "prudent":
        stars = min(stars, 3)
    return BetRecommendation(
        market=opp.market,
        pick=opp.pick,
        label=opp.label,
        cote=opp.cote,
        prob_pct=opp.prob_pct,
        edge=opp.edge,
        score=round(score, 4),
        tier=tier,
        raison=_raison(opp.prob_pct, opp.cote, opp.edge, tier),
        stars=stars,
    )


def build_intelligent_conseils(
    *,
    cotes_1n2: dict[str, float | None],
    probs: dict[str, int],
    markets: MarketsRaw,
    les_deux_marquent: int | None = None,
    prob_over_25_modele: int | None = None,
    prob_btts_modele: int | None = None,
    home: str = "",
    away: str = "",
    value_bets: list[ValueBet] | None = None,
    premium: PremiumAnalysis | None = None,
    pronostic_1n2: str | None = None,
    limit: int = ADVISOR_TOP_N,
) -> tuple[list[BetRecommendation], BetRecommendation | None]:
    """
    Parcourt tous les marchés scrapés + value bets et retourne le top N
    classé par score d'avantage (pas seulement proba ou cote isolée).
    """
    pool: list[_Opp] = []
    _collect_1n2_and_dc(
        pool,
        cotes_1n2=cotes_1n2,
        probs=probs,
        home=home,
        away=away,
        markets=markets,
    )
    _collect_dnb(pool, probs=probs, home=home, away=away, markets=markets)
    _collect_ou(pool, markets=markets, prob_over_25_modele=prob_over_25_modele)
    _collect_btts(
        pool,
        markets=markets,
        les_deux_marquent=les_deux_marquent,
        prob_btts_modele=prob_btts_modele,
    )
    _collect_team_goals(pool, markets=markets)
    _collect_half_time(pool, markets=markets, probs=probs, home=home, away=away)
    _collect_handicap(pool, markets=markets, home=home, away=away)
    _collect_exact_goals(pool, markets=markets)
    _collect_value_bets(pool, value_bets=value_bets or [])
    _collect_premium(pool, premium=premium)

    pool = _dedupe_dc_same_cote(pool)

    pick = str(pronostic_1n2 or "").strip()
    if pick in ("1", "N", "2"):

        def _oppose(o: _Opp) -> bool:
            if o.market == "1n2":
                return o.pick != pick
            if o.market == "dc_1x":
                return pick == "2"
            if o.market == "dc_x2":
                return pick == "1"
            return False

        pool = [o for o in pool if not _oppose(o)]

    seen: set[tuple[str, str]] = set()
    unique: list[_Opp] = []
    for o in pool:
        key = (o.market, o.pick)
        if key in seen:
            continue
        seen.add(key)
        unique.append(o)

    recs = [_to_recommendation(o) for o in unique if _advantage_score(o.prob_pct, o.cote, o.edge) > 0]
    recs.sort(key=lambda r: r.score, reverse=True)
    top = recs[:limit]
    best = top[0] if top else None
    return top, best
