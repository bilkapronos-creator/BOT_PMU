"""
Détecteurs Value Bets PREMIUM — Velora Engine v2 (B4).

Seuils stricts (config.EDGE_THRESHOLDS) : le marché doit fournir une probabilité
ou une estimation conservative ; edge = p × cote >= seuil.
"""

from __future__ import annotations

import re
from typing import Any

from velora_engine.analysis.probability import edge_score, stars_from_edge
from velora_engine.config import EDGE_THRESHOLDS
from velora_engine.models import (
    PremiumAnalysis,
    PremiumScoreExact,
    PremiumScorerMarket,
    ScoreExactRow,
    ScorerRow,
    ValueBet,
)
from velora_engine.scrape.markets_extractor import ExtractedMarkets

SCORE_EXACT_MIN_COTE = 7.50
BUTEUR_MIN_COTE_MATCH = 2.50
BUTEUR_MIN_COTE_DOUBLE = 8.00
FAVORI_BUTEUR_MAX = 1.70
FAVORI_1N2_STRONG = 1.80
MATCH_FERME_MOIN25_MAX = 1.65

PREMIUM_PRIORITY = (
    "score_exact",
    "buteur_match",
    "buteur_mi_temps",
    "buteur_double",
)


def _threshold(market: str) -> float:
    return EDGE_THRESHOLDS.get(market, 1.18)


def _make_vb(
    market: str,
    pick: str,
    label: str,
    cote: float | None,
    edge: float,
    *,
    stars: int | None = None,
) -> ValueBet:
    return ValueBet(
        market=market,
        pick=pick,
        label=label,
        cote=cote,
        edge=round(edge, 3),
        stars=stars if stars is not None else stars_from_edge(edge, max_stars=5),
        is_primary=False,
    )


def _favori_1n2(cotes: dict[str, float | None]) -> tuple[str | None, float | None]:
    try:
        c1 = float(cotes.get("1") or 0)
        c2 = float(cotes.get("2") or 0)
    except (TypeError, ValueError):
        return None, None
    if c1 <= 0 or c2 <= 0:
        return None, None
    if c1 <= c2:
        return "1", c1
    return "2", c2


def _normalize_score_tuple(label: str) -> tuple[int, int] | None:
    if not label:
        return None
    s = label.replace("–", "-").replace(" ", "")
    m = re.match(r"^(\d+)-(\d+)$", s)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _model_prob_buteur(
    cote: float,
    *,
    favori_odd: float | None,
    strict: bool = False,
) -> float | None:
    """
    Estimation conservative quand Winamax ne fournit pas de % buteur.
    Plus stricte pour MT / doublé.
    """
    if cote < 1.01:
        return None
    implied = 1.0 / cote
    if favori_odd is None or favori_odd >= FAVORI_BUTEUR_MAX:
        boost = 1.0
    else:
        boost = 1.22 if not strict else 1.12
    model = min(0.38 if not strict else 0.28, implied * boost)
    return model


def detect_value_score_exact(
    top3: list[ScoreExactRow],
    *,
    cotes_1n2: dict[str, float | None],
    markets_raw,
) -> ValueBet | None:
    """Score exact : prob Winamax × cote >= 1.15 (+ filtre contexte favori si dispo)."""
    fav_side, fav_odd = _favori_1n2(cotes_1n2)
    expected_scores: set[tuple[int, int]] | None = None
    if fav_side and fav_odd and fav_odd < FAVORI_1N2_STRONG:
        expected_scores = {(1, 0), (2, 0)} if fav_side == "1" else {(0, 1), (0, 2)}

    ou = markets_raw.over_under_total.get("2.5") if markets_raw else None
    if ou and ou.moins_cote is not None and ou.moins_cote >= MATCH_FERME_MOIN25_MAX:
        return None

    best: tuple[float, ValueBet] | None = None
    for row in top3:
        if row.prob is None or row.cote is None:
            continue
        if row.cote < SCORE_EXACT_MIN_COTE:
            continue
        tpl = _normalize_score_tuple(row.score)
        if expected_scores and tpl and tpl not in expected_scores:
            continue
        e = edge_score(row.prob, row.cote)
        if e is None or e < _threshold("score_exact"):
            continue
        score_short = row.score.replace(" - ", "-").replace(" – ", "-")
        vb = _make_vb(
            "score_exact",
            score_short,
            f"Score exact {score_short} @ {row.cote:.2f}",
            row.cote,
            e,
            stars=5 if e >= 1.25 else 4,
        )
        if best is None or e > best[0]:
            best = (e, vb)
    return best[1] if best else None


def detect_value_buteur_row(
    row: ScorerRow,
    market: str,
    *,
    favori_odd: float | None,
    min_cote: float,
    strict: bool,
) -> ValueBet | None:
    if row.cote < min_cote:
        return None
    prob_pct = getattr(row, "prob", None)
    if prob_pct is not None:
        model = float(prob_pct) / 100.0
    else:
        model = _model_prob_buteur(row.cote, favori_odd=favori_odd, strict=strict)
    if model is None:
        return None
    e = model * row.cote
    if e < _threshold(market):
        return None
    icons = {
        "buteur_match": "⚽",
        "buteur_mi_temps": "⏱️",
        "buteur_double": "🎲",
    }
    icon = icons.get(market, "⚽")
    return _make_vb(
        market,
        row.joueur,
        f"{icon} Buteur : {row.joueur} @ {row.cote:.2f}",
        row.cote,
        e,
        stars=5 if e >= 1.35 else 4,
    )


def detect_value_buteur_list(
    top: list[ScorerRow],
    market: str,
    *,
    cotes_1n2: dict[str, float | None],
    min_cote: float,
    strict: bool,
    require_favori: bool = True,
) -> ValueBet | None:
    _, fav_odd = _favori_1n2(cotes_1n2)
    if require_favori and (fav_odd is None or fav_odd >= FAVORI_BUTEUR_MAX):
        return None
    best: tuple[float, ValueBet] | None = None
    for row in top:
        vb = detect_value_buteur_row(
            row,
            market,
            favori_odd=fav_odd,
            min_cote=min_cote,
            strict=strict,
        )
        if vb and (best is None or vb.edge > best[0]):
            best = (vb.edge, vb)
    return best[1] if best else None


def detect_all_premium_values(
    extracted: ExtractedMarkets,
    *,
    cotes_1n2: dict[str, float | None],
    probs: dict[str, int] | None = None,
) -> PremiumAnalysis:
    """
    Remplit premium_analysis : top3 / tops toujours présents ;
    value_bets[] uniquement si edge >= seuils config.
    """
    score_block = PremiumScoreExact(top3=list(extracted.score_exact_top3))
    buteur_match_block = PremiumScorerMarket(top=list(extracted.buteur_match))
    buteur_mt_block = PremiumScorerMarket(top=list(extracted.buteur_mi_temps))
    buteur_double_block = PremiumScorerMarket(top=list(extracted.buteur_double))

    candidates: list[ValueBet] = []

    vb_score = detect_value_score_exact(
        extracted.score_exact_top3,
        cotes_1n2=cotes_1n2,
        markets_raw=extracted.markets_raw,
    )
    if vb_score:
        score_block.value_bet = vb_score
        candidates.append(vb_score)

    vb_bm = detect_value_buteur_list(
        extracted.buteur_match,
        "buteur_match",
        cotes_1n2=cotes_1n2,
        min_cote=BUTEUR_MIN_COTE_MATCH,
        strict=False,
    )
    if vb_bm:
        buteur_match_block.value_bet = vb_bm
        candidates.append(vb_bm)

    vb_mt = detect_value_buteur_list(
        extracted.buteur_mi_temps,
        "buteur_mi_temps",
        cotes_1n2=cotes_1n2,
        min_cote=BUTEUR_MIN_COTE_MATCH,
        strict=True,
    )
    if vb_mt:
        buteur_mt_block.value_bet = vb_mt
        candidates.append(vb_mt)

    vb_dbl = detect_value_buteur_list(
        extracted.buteur_double,
        "buteur_double",
        cotes_1n2=cotes_1n2,
        min_cote=BUTEUR_MIN_COTE_DOUBLE,
        strict=True,
        require_favori=True,
    )
    if vb_dbl:
        buteur_double_block.value_bet = vb_dbl
        candidates.append(vb_dbl)

    value_bets_sorted = sorted(
        candidates,
        key=lambda v: (
            PREMIUM_PRIORITY.index(v.market)
            if v.market in PREMIUM_PRIORITY
            else 99,
            -v.edge,
        ),
    )

    return PremiumAnalysis(
        score_exact=score_block,
        buteur_match=buteur_match_block,
        buteur_mi_temps=buteur_mt_block,
        buteur_double=buteur_double_block,
        value_bets=value_bets_sorted,
    )
