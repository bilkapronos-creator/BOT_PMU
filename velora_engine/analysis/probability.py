"""Probabilités implicites et calcul d'edge (p × cote)."""

from __future__ import annotations

from typing import Any


def safe_float(val: Any) -> float | None:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def implied_probabilities(cotes: dict[str, float | None]) -> dict[str, int]:
    inv: dict[str, float] = {}
    for key in ("1", "N", "2"):
        c = safe_float(cotes.get(key))
        if c and c > 1.0:
            inv[key] = 1.0 / c
    total = sum(inv.values())
    if total <= 0:
        return {"1": 33, "N": 34, "2": 33}
    raw = {k: int(round(100 * v / total)) for k, v in inv.items()}
    diff = 100 - sum(raw.values())
    if diff and raw:
        kmax = max(raw, key=raw.get)
        raw[kmax] += diff
    return raw


def edge_score(prob_pct: float | int | None, cote: float | None) -> float | None:
    if prob_pct is None or cote is None:
        return None
    try:
        p = float(prob_pct) / 100.0
        c = float(cote)
        if p <= 0 or c <= 1.0:
            return None
        return p * c
    except (TypeError, ValueError):
        return None


def stars_from_edge(edge: float, *, max_stars: int = 5) -> int:
    if edge >= 1.20:
        return min(max_stars, 5)
    if edge >= 1.12:
        return min(max_stars, 4)
    if edge >= 1.06:
        return min(max_stars, 3)
    return 2
