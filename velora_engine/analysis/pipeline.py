"""
Pipeline Velora v2 — remplit free_analysis / premium_analysis (B3).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from velora_engine.analysis.pro_context import build_confidence
from velora_engine.analysis.schedule_risk import (
    build_schedule_index,
    check_upcoming_schedule_risk,
)
from velora_engine.analysis.value_detectors import detect_all_free_values
from velora_engine.config import ENGINE_ID, SCHEMA_VERSION
from velora_engine.models import (
    ApiVeloraDocument,
    FreeAnalysis,
    MatchRecordV2,
    MetaMatch,
    document_to_json,
)
from velora_engine.output.legacy_adapter import legacy_shim_from_v2, premium_from_extracted
from velora_engine.scrape.markets_extractor import extract_all_markets

# Réutilise le parseur existant (cotes 1N2, filtres horizon)
from parser_winamax import (  # noqa: E402
    FOOTBALL_SPORT_ID,
    apply_velora_analysis,
    bets_for_match as parser_bets_for_match,
    build_velora_record,
    extract_1n2_from_bet,
    finalize_probabilities,
    find_main_bet,
    format_match_start,
    get_teams,
    is_match_live,
    is_match_within_parser_horizon,
    is_winamax_match_finished,
)
from velora_intel import extract_intel_from_state, intel_stats_suffisantes  # noqa: E402

try:
    from zoneinfo import ZoneInfo

    TZ_PARIS = ZoneInfo("Europe/Paris")
except Exception:
    TZ_PARIS = timezone.utc


def _apply_intel_overlay(legacy: dict, state: dict | None, match_id: str) -> dict:
    """Enrichit indice / score depuis PRELOADED_STATE si forme dispo."""
    if not state:
        return legacy
    intel = extract_intel_from_state(state, match_id)
    if not intel_stats_suffisantes(intel):
        return legacy
    from velora_intel import apply_statistical_velora_analysis

    return apply_statistical_velora_analysis(legacy, intel)


def build_match_v2(
    *,
    state: dict,
    match_id: str,
    raw_match: dict,
    home: str,
    away: str,
    schedule_index: dict,
) -> MatchRecordV2 | None:
    mid = str(raw_match.get("matchId") or match_id)
    sort_ts, date_match = format_match_start(raw_match.get("matchStart"))
    mbets = parser_bets_for_match(state.get("bets") or {}, mid)
    bet = find_main_bet(mbets, raw_match.get("mainBetId"))
    if not bet:
        return None
    outcomes = state.get("outcomes") or {}
    odds = state.get("odds")
    cotes, raw_pct = extract_1n2_from_bet(bet, outcomes, odds)
    probs = finalize_probabilities(raw_pct, cotes)
    cotes_out = {"1": cotes.get("1"), "N": cotes.get("N"), "2": cotes.get("2")}
    probs_out = {"1": probs.get("1", 0), "N": probs.get("N", 0), "2": probs.get("2", 0)}

    legacy = build_velora_record(
        mid,
        home,
        away,
        cotes,
        probs,
        mbets,
        outcomes,
        date_match,
        sort_ts,
        odds,
        match_status=raw_match.get("status"),
    )
    legacy = _apply_intel_overlay(legacy, state, mid)

    extracted = extract_all_markets(
        mbets,
        outcomes,
        odds,
        home=home,
        away=away,
        raw_match=raw_match,
        state=state,
    )

    free_values = detect_all_free_values(
        cotes_1n2=cotes_out,
        probs=probs_out,
        markets=extracted.markets_raw,
        les_deux_marquent=legacy.get("les_deux_marquent"),
        home=home,
        away=away,
    )

    free = FreeAnalysis(
        cotes_1n2=cotes_out,
        probabilites=probs_out,
        markets_raw=extracted.markets_raw,
        value_bets=free_values.value_bets,
        primary_pick=free_values.primary_pick,
        display_badges=free_values.display_badges,
    )

    premium = premium_from_extracted(
        extracted,
        cotes_1n2=cotes_out,
        probs=probs_out,
    )

    pro_alerts: list = []
    rotation = check_upcoming_schedule_risk(
        match_id=mid,
        home=home,
        away=away,
        match_start_ts=sort_ts,
        cotes_1n2=cotes_out,
        current_competition=extracted.competition,
        schedule_index=schedule_index,
    )
    if rotation:
        pro_alerts.append(rotation)

    best_edge = max((v.edge for v in free.value_bets), default=None)
    confidence = build_confidence(
        velora_score=legacy.get("velora_score"),
        indice_velora=int(legacy.get("indice_velora") or 0),
        indice_label=legacy.get("indice_velora_label"),
        competition=extracted.competition,
        pro_alerts=pro_alerts,
        best_edge=best_edge,
    )

    record = MatchRecordV2(
        id_match=mid,
        date_match=date_match,
        match_start_ts=sort_ts,
        match_status=str(legacy.get("match_status") or "PREMATCH").upper(),
        equipe_domicile=home,
        equipe_exterieur=away,
        meta_match=MetaMatch(extracted.competition),
        confidence=confidence,
        pro_alerts=pro_alerts,
        free_analysis=free,
        premium_analysis=premium,
        legacy=legacy_shim_from_v2(free, extra=legacy),
    )
    return record


def build_api_document_from_state(state: dict | None) -> ApiVeloraDocument:
    """Construit api_velora_matchs.json v2 depuis PRELOADED_STATE."""
    if not state or not isinstance(state, dict):
        return ApiVeloraDocument(
            schema_version=SCHEMA_VERSION,
            meta={
                "generated_at": datetime.now(TZ_PARIS).isoformat(),
                "engine": ENGINE_ID,
                "match_count": 0,
                "error": "state_empty",
            },
            matchs=[],
        )

    schedule_index = build_schedule_index(state)
    matches_map = state.get("matches") or {}
    now = datetime.now(tz=TZ_PARIS)
    records: list[MatchRecordV2] = []

    for match_id, raw in matches_map.items():
        if not isinstance(raw, dict) or raw.get("sportId") != FOOTBALL_SPORT_ID:
            continue
        if is_match_live(raw):
            continue
        home, away = get_teams(raw)
        if home == "?" and away == "?":
            continue
        sort_ts, _ = format_match_start(raw.get("matchStart"))
        if is_winamax_match_finished(raw, sort_ts, now):
            continue
        if not is_match_within_parser_horizon(raw, sort_ts, now):
            continue
        built = build_match_v2(
            state=state,
            match_id=str(match_id),
            raw_match=raw,
            home=home,
            away=away,
            schedule_index=schedule_index,
        )
        if built:
            records.append(built)

    records.sort(key=lambda m: m.match_start_ts or 2**62)

    return ApiVeloraDocument(
        schema_version=SCHEMA_VERSION,
        meta={
            "generated_at": datetime.now(TZ_PARIS).isoformat(),
            "engine": ENGINE_ID,
            "match_count": len(records),
        },
        matchs=records,
    )


def write_api_json(path, state: dict | None) -> int:
    """Écrit le document v2 sur disque. Retourne le nombre de matchs."""
    doc = build_api_document_from_state(state)
    path.write_text(document_to_json(doc), encoding="utf-8")
    return len(doc.matchs)
