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
        or match.get("pronostic_1n2")
        or free.get("pronostic_1n2")
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
        vals = [p1, n, p2]
        if max(vals) - min(vals) <= 10:
            return True
        return False
    except (TypeError, ValueError):
        return False


def _value_pick_opposes_pronostic(official: str, market: str | None, pick: str | None) -> bool:
    """True si un value bet 1N2 / DC contredit le pronostic modèle Velora."""
    if not official or not pick:
        return False
    mk = str(market or "").lower()
    pk = str(pick).strip()
    if mk == "1n2":
        return pk != official
    if mk == "dc_1x":
        return official == "2"
    if mk == "dc_x2":
        return official == "1"
    return False


def sanitize_conflicting_value_picks(match: dict[str, Any]) -> dict[str, Any]:
    """Retire primary_pick / value_bets 1N2 qui contredisent pronostic_1n2 / velora_pick."""
    updated = dict(match)
    official = pronostic_pick_from_match(updated)
    if official not in ("1", "N", "2", "dc_1x", "dc_x2"):
        return updated

    free = dict(updated.get("free_analysis") or {})

    def _strip_pp(container: dict[str, Any], key: str = "primary_pick") -> None:
        pp = container.get(key)
        if isinstance(pp, dict) and _value_pick_opposes_pronostic(
            official, pp.get("market"), pp.get("pick")
        ):
            container.pop(key, None)

    _strip_pp(free)
    _strip_pp(updated)

    vbs = free.get("value_bets")
    if isinstance(vbs, list):
        free["value_bets"] = [
            v
            for v in vbs
            if isinstance(v, dict)
            and not _value_pick_opposes_pronostic(official, v.get("market"), v.get("pick"))
        ]
        if not free.get("primary_pick") and free["value_bets"]:
            best = max(free["value_bets"], key=lambda v: float(v.get("edge") or 0))
            free["primary_pick"] = {
                "market": best.get("market"),
                "pick": best.get("pick"),
                "label": best.get("label"),
                "cote": best.get("cote"),
                "conseil_short": best.get("label"),
            }

    if not free.get("value_bets"):
        free.pop("primary_pick", None)

    cur_probs = free.get("probabilites") or updated.get("probabilites")
    cotes = free.get("cotes_1n2") or updated.get("cotes") or {}
    if probas_1n2_suspectes(cur_probs) and any(cotes.get(k) for k in ("1", "N", "2")):
        poisson = build_poisson_analysis(
            cotes=cotes,
            intel=updated.get("velora_intel"),
            markets=MarketsRaw(),
        )
        free["probabilites"] = poisson.probabilites_1n2
        updated["probabilites"] = poisson.probabilites_1n2

    updated["free_analysis"] = free
    if isinstance(updated.get("primary_pick"), dict) and not free.get("primary_pick"):
        updated.pop("primary_pick", None)

    pp = free.get("primary_pick") or updated.get("primary_pick")
    if isinstance(pp, dict) and _value_pick_opposes_pronostic(official, pp.get("market"), pp.get("pick")):
        free.pop("primary_pick", None)
        updated.pop("primary_pick", None)

    legacy = updated.get("_legacy")
    if isinstance(legacy, dict):
        leg_pp = legacy.get("opportunite_type")
        if str(leg_pp or "").lower() in ("1n2", "dc_1x", "dc_x2") and not free.get("value_bets"):
            legacy["is_opportunite"] = False
            legacy.pop("opportunite_type", None)

    if str(updated.get("opportunite_type") or "").lower() in ("1n2", "dc_1x", "dc_x2"):
        if not free.get("value_bets"):
            updated.pop("opportunite_type", None)
            updated.pop("opportunite_detail", None)
            updated["is_opportunite"] = False

    return updated


def ensure_match_scores_coherent(match: dict[str, Any]) -> dict[str, Any]:
    """Scores + probas affichables alignés sur le pronostic Velora (tous matchs)."""
    updated = dict(match)
    pick = pronostic_pick_from_match(updated)
    if pick not in ("1", "N", "2", "dc_1x", "dc_x2"):
        return sanitize_conflicting_value_picks(updated)

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

    updated["velora_pick_1n2"] = pick
    free["pronostic_1n2"] = pick
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

    return sanitize_conflicting_value_picks(updated)


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
