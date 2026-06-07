"""
Modèle Poisson « maison » — buts attendus depuis forme + marchés Winamax (0 €).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from velora_engine.analysis.probability import implied_probabilities, safe_float
from velora_engine.models import MarketsRaw
from velora_intel import intel_stats_suffisantes

LEAGUE_GOALS_HOME = 1.45
LEAGUE_GOALS_AWAY = 1.20
HOME_ADV_FACTOR = 1.10
BASE_ATTACK = 1.35
MAX_GOALS_MATRIX = 6


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam**k) / math.factorial(k)


def _normalize_pct_dict(raw: dict[str, float]) -> dict[str, int]:
    total = sum(max(0.0, float(raw.get(k) or 0)) for k in ("1", "N", "2"))
    if total <= 0:
        return {"1": 33, "N": 34, "2": 33}
    scaled = {k: 100.0 * max(0.0, float(raw.get(k) or 0)) / total for k in ("1", "N", "2")}
    out = {k: int(round(scaled[k])) for k in ("1", "N", "2")}
    diff = 100 - sum(out.values())
    if diff:
        kmax = max(out, key=out.get)
        out[kmax] += diff
    return out


def attack_defense_from_form(form: dict[str, Any] | None) -> tuple[float, float]:
    """Proxy buts marqués / encaissés par match sur la forme récente."""
    form = form or {}
    played = max(1, int(form.get("played") or 0))
    wins = int(form.get("wins") or 0)
    draws = int(form.get("draws") or 0)
    losses = int(form.get("losses") or 0)
    goals_for = BASE_ATTACK + 0.42 * (wins / played) + 0.12 * (draws / played)
    goals_against = BASE_ATTACK + 0.42 * (losses / played) + 0.12 * (draws / played)
    return (
        min(3.2, max(0.55, goals_for)),
        min(3.2, max(0.55, goals_against)),
    )


def lambda_total_from_over25_prob(p_over: float) -> float:
    """λ total tel que P(X > 2.5) ≈ p_over pour X ~ Poisson(λ)."""
    p_over = min(0.92, max(0.08, float(p_over)))
    lo, hi = 0.4, 5.5
    for _ in range(40):
        mid = (lo + hi) / 2.0
        p_under = sum(_poisson_pmf(k, mid) for k in (0, 1, 2))
        if 1.0 - p_under < p_over:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 3)


def estimate_lambdas(
    cotes: dict[str, float | None],
    intel: dict[str, Any] | None,
    markets: MarketsRaw | None,
) -> tuple[float, float]:
    intel = intel or {}
    markets = markets or MarketsRaw()
    hf = intel.get("home_form") or {}
    af = intel.get("away_form") or {}
    fd_gf_h = intel.get("fd_home_goals_for")
    fd_ga_h = intel.get("fd_home_goals_against")
    fd_gf_a = intel.get("fd_away_goals_for")
    fd_ga_a = intel.get("fd_away_goals_against")
    if isinstance(fd_gf_h, (int, float)) and isinstance(fd_ga_h, (int, float)):
        att_h, def_h = float(fd_gf_h), float(fd_ga_h)
    else:
        att_h, def_h = attack_defense_from_form(hf if hf.get("played") else None)
    if isinstance(fd_gf_a, (int, float)) and isinstance(fd_ga_a, (int, float)):
        att_a, def_a = float(fd_gf_a), float(fd_ga_a)
    else:
        att_a, def_a = attack_defense_from_form(af if af.get("played") else None)

    lam_home = (att_h / BASE_ATTACK) * (def_a / BASE_ATTACK) * LEAGUE_GOALS_HOME * HOME_ADV_FACTOR
    lam_away = (att_a / BASE_ATTACK) * (def_h / BASE_ATTACK) * LEAGUE_GOALS_AWAY

    if not hf.get("played") and not af.get("played"):
        lam_home, lam_away = LEAGUE_GOALS_HOME * HOME_ADV_FACTOR, LEAGUE_GOALS_AWAY

    marche = implied_probabilities(cotes)
    p1 = max(1.0, float(marche.get("1") or 33))
    p2 = max(1.0, float(marche.get("2") or 33))
    strength_ratio = math.sqrt(p1 / p2)
    total_form = lam_home + lam_away
    if total_form > 0.2:
        lam_home = total_form * strength_ratio / (1.0 + strength_ratio)
        lam_away = total_form - lam_home

    ou25 = markets.over_under_total.get("2.5")
    target_total: float | None = None
    if ou25:
        if ou25.plus_prob is not None:
            target_total = lambda_total_from_over25_prob(ou25.plus_prob / 100.0)
        elif ou25.plus_cote and ou25.moins_cote:
            cp = safe_float(ou25.plus_cote)
            cm = safe_float(ou25.moins_cote)
            if cp and cm and cp > 1.0 and cm > 1.0:
                p_over = (1.0 / cp) / ((1.0 / cp) + (1.0 / cm))
                target_total = lambda_total_from_over25_prob(p_over)

    if target_total and lam_home + lam_away > 0.2:
        scale = target_total / (lam_home + lam_away)
        lam_home = max(0.35, lam_home * scale)
        lam_away = max(0.35, lam_away * scale)

    return (
        round(min(4.5, max(0.25, lam_home)), 3),
        round(min(4.5, max(0.25, lam_away)), 3),
    )


def score_probability_matrix(
    lam_home: float,
    lam_away: float,
    max_goals: int = MAX_GOALS_MATRIX,
) -> list[list[float]]:
    return [
        [_poisson_pmf(i, lam_home) * _poisson_pmf(j, lam_away) for j in range(max_goals + 1)]
        for i in range(max_goals + 1)
    ]


def probabilities_1n2_from_matrix(matrix: list[list[float]]) -> dict[str, int]:
    p1 = pn = p2 = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if i > j:
                p1 += p
            elif i == j:
                pn += p
            else:
                p2 += p
    return _normalize_pct_dict({"1": p1, "N": pn, "2": p2})


def prob_over_25_from_matrix(matrix: list[list[float]]) -> int:
    total = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if i + j >= 3:
                total += p
    return int(round(min(99.0, max(1.0, total * 100.0))))


def prob_btts_oui_from_matrix(matrix: list[list[float]]) -> int:
    total = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if i >= 1 and j >= 1:
                total += p
    return int(round(min(99.0, max(1.0, total * 100.0))))


def _score_outcome_1n2(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "1"
    if home_goals < away_goals:
        return "2"
    return "N"


def _score_matches_pick(home_goals: int, away_goals: int, pick: str) -> bool:
    pick = str(pick or "").strip()
    if pick in ("1", "N", "2"):
        return _score_outcome_1n2(home_goals, away_goals) == pick
    if pick == "dc_1x":
        return home_goals >= away_goals
    if pick == "dc_x2":
        return home_goals <= away_goals
    return True


def _parse_score_label(label: str) -> tuple[int, int] | None:
    raw = str(label or "").strip().replace(" ", "")
    if "-" not in raw:
        return None
    parts = raw.split("-", 1)
    try:
        return int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None


def top_scores_from_matrix(
    matrix: list[list[float]],
    *,
    limit: int = 5,
    pick: str | None = None,
) -> list[dict[str, Any]]:
    scores: list[tuple[float, str]] = []
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if p <= 0:
                continue
            if pick and not _score_matches_pick(i, j, pick):
                continue
            scores.append((p, f"{i}-{j}"))
    scores.sort(key=lambda x: x[0], reverse=True)
    out: list[dict[str, Any]] = []
    for p, label in scores[:limit]:
        prob = int(round(p * 100.0))
        if prob < 1:
            continue
        out.append({"score": label, "prob": prob})
    return out


def align_top_scores_for_pick(
    scores: list[dict[str, Any]],
    pick: str | None,
    *,
    matrix: list[list[float]] | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Garde uniquement les scores cohérents avec le pronostic 1N2 affiché."""
    pick = str(pick or "").strip()
    if pick not in ("1", "N", "2", "dc_1x", "dc_x2"):
        return scores[:limit]
    filtered: list[dict[str, Any]] = []
    for row in scores:
        if not isinstance(row, dict):
            continue
        parsed = _parse_score_label(str(row.get("score") or ""))
        if not parsed:
            continue
        if _score_matches_pick(parsed[0], parsed[1], pick):
            filtered.append(row)
    if filtered:
        if len(filtered) < limit and matrix is not None:
            seen = {str(r.get("score") or "") for r in filtered}
            for row in top_scores_from_matrix(matrix, limit=limit * 2, pick=pick):
                key = str(row.get("score") or "")
                if key and key not in seen:
                    filtered.append(row)
                    seen.add(key)
                if len(filtered) >= limit:
                    break
        return filtered[:limit]
    if matrix is not None:
        return top_scores_from_matrix(matrix, limit=limit, pick=pick)
    return []


def poisson_blend_weight(
    intel: dict[str, Any] | None,
    markets: MarketsRaw | None,
) -> float:
    w = 0.22
    if intel_stats_suffisantes(intel or {}):
        w += 0.18
    markets = markets or MarketsRaw()
    ou25 = markets.over_under_total.get("2.5")
    if ou25 and (ou25.plus_prob is not None or ou25.plus_cote):
        w += 0.12
    return min(0.50, w)


def blend_probability_dicts(
    base: dict[str, int],
    poisson: dict[str, int],
    poisson_weight: float,
) -> dict[str, int]:
    pw = min(0.55, max(0.0, float(poisson_weight)))
    blend: dict[str, float] = {}
    for key in ("1", "N", "2"):
        blend[key] = (1.0 - pw) * float(base.get(key) or 0) + pw * float(poisson.get(key) or 0)
    return _normalize_pct_dict(blend)


@dataclass
class PoissonAnalysis:
    lambda_home: float
    lambda_away: float
    probabilites_1n2: dict[str, int]
    prob_over_25: int
    prob_btts_oui: int
    top_scores: list[dict[str, Any]]
    blend_weight: float
    matrix: list[list[float]] | None = None


def build_poisson_analysis(
    *,
    cotes: dict[str, float | None],
    intel: dict[str, Any] | None,
    markets: MarketsRaw | None,
) -> PoissonAnalysis:
    lam_h, lam_a = estimate_lambdas(cotes, intel, markets)
    matrix = score_probability_matrix(lam_h, lam_a)
    return PoissonAnalysis(
        lambda_home=lam_h,
        lambda_away=lam_a,
        probabilites_1n2=probabilities_1n2_from_matrix(matrix),
        prob_over_25=prob_over_25_from_matrix(matrix),
        prob_btts_oui=prob_btts_oui_from_matrix(matrix),
        top_scores=top_scores_from_matrix(matrix),
        blend_weight=poisson_blend_weight(intel, markets),
        matrix=matrix,
    )
