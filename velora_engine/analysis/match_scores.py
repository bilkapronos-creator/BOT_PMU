"""Cohérence scores exacts ↔ pronostic 1N2 affiché."""

from __future__ import annotations

from typing import Any

from velora_engine.analysis.model_poisson import (
    _parse_score_label,
    _score_matches_pick,
    align_top_scores_for_pick,
    build_poisson_analysis,
)
from velora_engine.models import MarketsRaw
from parser_winamax import finalize_score_rows_probs


def pronostic_pick_from_match(match: dict[str, Any]) -> str:
    free = match.get("free_analysis") or {}
    pick = (
        match.get("velora_pick_1n2")
        or free.get("pronostic_1n2")
        or (free.get("primary_pick") or {}).get("pick")
    )
    return str(pick or "").strip()


def scores_coherent_with_pick(scores: list[dict[str, Any]], pick: str) -> bool:
    if pick not in ("1", "N", "2", "dc_1x", "dc_x2") or not scores:
        return False
    checked = 0
    for row in scores[:5]:
        if not isinstance(row, dict):
            continue
        parsed = _parse_score_label(str(row.get("score") or ""))
        if not parsed:
            continue
        checked += 1
        if not _score_matches_pick(parsed[0], parsed[1], pick):
            return False
    return checked > 0


def probas_1n2_suspectes(probs: dict[str, Any] | None) -> bool:
    if not isinstance(probs, dict):
        return False
    try:
        n = int(probs.get("N") or 0)
        p1 = int(probs.get("1") or 0)
        p2 = int(probs.get("2") or 0)
        if n == 0 and p1 + p2 >= 99 and abs(p1 - p2) <= 5:
            return True
        return False
    except (TypeError, ValueError):
        return False


def ensure_match_scores_coherent(match: dict[str, Any]) -> dict[str, Any]:
    """Scores + probas affichables alignés sur le pronostic Velora (tous matchs)."""
    updated = dict(match)
    pick = pronostic_pick_from_match(updated)
    if pick not in ("1", "N", "2", "dc_1x", "dc_x2"):
        return updated

    free = dict(updated.get("free_analysis") or {})
    cotes = free.get("cotes_1n2") or updated.get("cotes") or {}
    intel = updated.get("velora_intel")
    aligned: list[dict[str, Any]] = []

    model_scores = free.get("top_scores_modele")
    if isinstance(model_scores, list) and scores_coherent_with_pick(model_scores, pick):
        aligned = model_scores[:5]
    elif any(cotes.get(k) for k in ("1", "N", "2")):
        poisson = build_poisson_analysis(cotes=cotes, intel=intel, markets=MarketsRaw())
        aligned = align_top_scores_for_pick(
            poisson.top_scores,
            pick,
            matrix=poisson.matrix,
            limit=5,
        )
        free["top_scores_modele"] = aligned
        free.setdefault(
            "poisson_lambdas",
            {"home": poisson.lambda_home, "away": poisson.lambda_away},
        )
        cur = free.get("probabilites") or updated.get("probabilites")
        if probas_1n2_suspectes(cur):
            free["probabilites"] = poisson.probabilites_1n2
            updated["probabilites"] = poisson.probabilites_1n2
        free.setdefault("pronostic_1n2", pick)
    else:
        raw_top = updated.get("top_scores")
        if isinstance(raw_top, list):
            aligned = align_top_scores_for_pick(
                finalize_score_rows_probs(list(raw_top)),
                pick,
                limit=5,
            )

    if aligned:
        updated["top_scores"] = aligned
        free["top_scores_modele"] = aligned
        updated["free_analysis"] = free

    exact = updated.get("score_exact")
    if isinstance(exact, list) and exact:
        aligned_exact = align_top_scores_for_pick(
            finalize_score_rows_probs(list(exact)),
            pick,
            limit=5,
        )
        if aligned_exact:
            updated["score_exact"] = aligned_exact

    return updated


def sanitize_matchs_document(data: Any) -> Any:
    """Applique ensure_match_scores_coherent à une liste ou un document v2."""
    if isinstance(data, list):
        return [ensure_match_scores_coherent(dict(m)) for m in data if isinstance(m, dict)]
    if isinstance(data, dict) and isinstance(data.get("matchs"), list):
        out = dict(data)
        out["matchs"] = [
            ensure_match_scores_coherent(dict(m)) for m in data["matchs"] if isinstance(m, dict)
        ]
        return out
    return data
