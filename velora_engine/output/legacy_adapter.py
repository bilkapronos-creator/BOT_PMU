"""Pont v1 ↔ v2 — nœud `_legacy` (TTL 2 semaines)."""

from __future__ import annotations

from typing import Any

from velora_engine.models import FreeAnalysis, LegacyShim, MatchRecordV2, PremiumAnalysis


def _marches_supplementaires_from_markets(free: FreeAnalysis) -> dict[str, Any]:
    pm: dict[str, Any] = {}
    for line, ou in free.markets_raw.over_under_total.items():
        pm[line] = ou.to_dict()
    ms: dict[str, Any] = {
        "plus_moins_buts": pm or None,
        "buteur_match": None,
        "buteur_mi_temps": None,
        "buteur_multiple": None,
    }
    if free.markets_raw.btts:
        pass
    return ms


def legacy_shim_from_v2(
    free: FreeAnalysis,
    *,
    extra: dict[str, Any] | None = None,
) -> LegacyShim:
    conseil = None
    if free.primary_pick:
        conseil = free.primary_pick.conseil_short
    tendance = "Match Tactique"
    ou = free.markets_raw.over_under_total.get("2.5")
    if ou and ou.plus_prob and ou.plus_prob > 55:
        tendance = "Match Offensif"
    shim = LegacyShim(
        conseil=conseil,
        tendance_buts=tendance,
        marches_supplementaires=_marches_supplementaires_from_markets(free),
        opportunite_type=free.primary_pick.market if free.primary_pick else None,
        is_opportunite=bool(free.value_bets),
    )
    if extra:
        if extra.get("conseil"):
            shim.conseil = extra["conseil"]
        if extra.get("tendance_buts"):
            shim.tendance_buts = extra["tendance_buts"]
        if extra.get("velora_intel"):
            pass
    return shim


def premium_from_extracted(
    extracted,
    *,
    cotes_1n2: dict | None = None,
    probs: dict[str, int] | None = None,
) -> PremiumAnalysis:
    from velora_engine.analysis.premium_value_detectors import detect_all_premium_values

    cotes = cotes_1n2 or {"1": None, "N": None, "2": None}
    return detect_all_premium_values(extracted, cotes_1n2=cotes, probs=probs)
