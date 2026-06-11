"""
Enrichit api_velora_matchs.json via les pages détail Winamax (PRELOADED_STATE).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

from parser_winamax import (
    apply_annex_markets,
    apply_velora_analysis,
    bets_for_match,
    finalize_score_rows_probs,
    is_match_live,
    lookup,
    lookup_odd,
    tendance_buts,
    _collect_buteur_rows,
    _extract_scorer_player_name,
    _is_scorer_market_bet,
)
from velora_intel import (
    apply_non_calculable_velora,
    apply_statistical_velora_analysis,
    intel_stats_suffisantes,
    extract_intel_from_page,
    extract_intel_from_state,
)
from velora_engine.analysis.match_scores import ensure_match_scores_coherent
from velora_engine.scrape.winamax_state import (
    proxy_interactive_enabled,
    proxy_user_data_dir,
    resolve_playwright_proxy_config,
    wait_for_proxy_authentication,
)
from velora_engine.analysis.model_poisson import align_top_scores_for_pick

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _chromium_headless() -> bool:
    force = os.environ.get("VELORA_HEADLESS", "").strip().lower()
    if force in ("0", "false", "no"):
        return False
    if force in ("1", "true", "yes"):
        return True
    if os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true":
        return False
    return os.environ.get("CI", "").strip().lower() in ("1", "true")


def _open_playwright_session(playwright) -> tuple[object | None, object, object]:
    """Retourne (browser ou None, context, page)."""
    proxy_url = os.environ.get("VELORA_PROXY_URL", "").strip()
    interactive_proxy = proxy_interactive_enabled() and bool(proxy_url)
    headless = False if interactive_proxy else _chromium_headless()
    print(f"[sniper] chromium headless={headless}")
    launch_kwargs = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    }
    proxy_cfg = resolve_playwright_proxy_config()
    if proxy_cfg:
        launch_kwargs["proxy"] = proxy_cfg
        mode = "interactif" if interactive_proxy else "automatique"
        print(f"[sniper] Proxy ({mode}): {proxy_cfg.get('server')}")
    if interactive_proxy:
        profile = proxy_user_data_dir()
        profile.mkdir(parents=True, exist_ok=True)
        context = playwright.chromium.launch_persistent_context(
            str(profile),
            user_agent=UA,
            locale="fr-FR",
            timezone_id="Europe/Paris",
            viewport={"width": 1920, "height": 1080},
            **launch_kwargs,
        )
        page = context.pages[0] if context.pages else context.new_page()
        return None, context, page
    browser = playwright.chromium.launch(**launch_kwargs)
    context = browser.new_context(
        user_agent=UA,
        locale="fr-FR",
        timezone_id="Europe/Paris",
        extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9"},
    )
    page = context.new_page()
    return browser, context, page

IN_PATH = Path(__file__).resolve().parent / "api_velora_matchs.json"
OUT_PATH = Path(__file__).resolve().parent / "api_velora_premium.json"
MATCH_URL = "https://www.winamax.fr/paris-sportifs/match/{id_match}"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
UNAVAILABLE = "Données indisponibles"
INDISPONIBLE = "Indisponible"
GOAL_LINES = ("1.5", "2.5", "3.5")
SNIPER_LIMIT = int(os.environ.get("SNIPER_LIMIT", "25"))
PAUSE_SECONDS = int(os.environ.get("SNIPER_PAUSE", "4"))
SNIPER_WINDOW_HOURS = int(os.environ.get("SNIPER_WINDOW_HOURS", "24"))

from parser_winamax import (  # noqa: E402
    MATCH_PARSER_HORIZON_MODE,
    is_kickoff_in_betting_day,
    parser_window_end,
)
SNIPER_PAST_GRACE_HOURS = int(os.environ.get("SNIPER_PAST_GRACE_HOURS", "2"))
TZ_PARIS = ZoneInfo("Europe/Paris")
DATE_MATCH_FMT = "%d/%m/%Y à %H:%M"


def _pct_int(out: dict) -> int | None:
    try:
        prob = out.get("percentDistribution") or out.get("probability")
        if prob is None:
            return None
        p = float(prob)
        if 0 < p <= 1:
            return int(round(p * 100))
        return int(round(p))
    except Exception:
        return None


def _top_scores_empty(val) -> bool:
    if val is None:
        return True
    if val == [] or val == "":
        return True
    if isinstance(val, str) and val.strip().lower() in ("", "données indisponibles"):
        return True
    return False


def _btts_empty(val) -> bool:
    if val is None:
        return True
    if val == "":
        return True
    if isinstance(val, str) and val.strip().lower() in ("", "données indisponibles"):
        return True
    return False


def needs_enrichment(match: dict) -> bool:
    return _top_scores_empty(match.get("top_scores")) or _btts_empty(
        match.get("les_deux_marquent")
    )


def match_start_datetime(match: dict) -> datetime | None:
    """Datetime Paris du coup d'envoi (match_start_ts ou date_match)."""
    raw_ts = match.get("match_start_ts")
    if raw_ts is not None:
        try:
            ts = float(raw_ts)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=TZ_PARIS)
        except (TypeError, ValueError, OSError, OverflowError):
            pass

    raw_date = match.get("date_match")
    if raw_date:
        try:
            return datetime.strptime(str(raw_date).strip(), DATE_MATCH_FMT).replace(
                tzinfo=TZ_PARIS
            )
        except ValueError:
            pass

    for key in ("matchStart", "matchStartDate"):
        raw = match.get(key)
        if raw is None:
            continue
        try:
            ts = float(raw)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=TZ_PARIS)
        except (TypeError, ValueError, OSError, OverflowError):
            continue
    return None


def is_within_sniper_window(
    match: dict, now: datetime | None = None
) -> bool:
    """True si le match est dans la fenêtre journée+nocturnes (ou N h glissantes)."""
    kickoff = match_start_datetime(match)
    if kickoff is None:
        return False
    now = now or datetime.now(tz=TZ_PARIS)
    if MATCH_PARSER_HORIZON_MODE != "rolling":
        return is_kickoff_in_betting_day(kickoff, now)
    earliest = now - timedelta(hours=SNIPER_PAST_GRACE_HOURS)
    latest = parser_window_end(now, SNIPER_WINDOW_HOURS)
    return earliest <= kickoff <= latest


def describe_skip_delay(match: dict, now: datetime) -> str:
    kickoff = match_start_datetime(match)
    if kickoff is None:
        return "date inconnue"
    delta = kickoff - now
    secs = delta.total_seconds()
    if secs < 0:
        hours_ago = int(abs(secs) // 3600)
        if hours_ago < 1:
            return "déjà commencé"
        return f"déjà commencé (il y a {hours_ago} h)"
    days = delta.days
    if days >= 1:
        suffix = "s" if days > 1 else ""
        return f"prévu dans {days} jour{suffix}"
    hours = int(secs // 3600)
    if hours >= 1:
        return f"prévu dans {hours} h"
    minutes = max(1, int(secs // 60))
    return f"prévu dans {minutes} min"


def stamp_skipped_match(match: dict) -> dict:
    """Structure premium sans requête HTTP (marchés indisponibles)."""
    rec = dict(match)
    rec["marches_supplementaires"] = default_marches_supplementaires()
    return rec


def extract_score_exact(
    match_bets: list, outcomes: dict, odds: dict | None = None
) -> list[dict] | str:
    try:
        for bet in match_bets:
            name = str(bet.get("betTypeName") or bet.get("betTitle") or "").strip().lower()
            if "score exact" not in name:
                continue
            if "multichance" in name or "mi-temps" in name or "mi temps" in name:
                continue
            rows = []
            for oid in bet.get("outcomes") or []:
                out = lookup(outcomes, oid)
                if not out:
                    continue
                label = str(out.get("label") or "").strip()
                pct = _pct_int(out)
                if not label or pct is None:
                    continue
                row = {"score": label, "prob": pct}
                if odds:
                    price = lookup_odd(odds, oid)
                    if price is not None:
                        row["cote"] = round(float(price), 2)
                rows.append(row)
            if rows:
                rows.sort(key=lambda x: x.get("prob", 0), reverse=True)
                return finalize_score_rows_probs(rows)[:3]
    except Exception:
        pass
    return UNAVAILABLE


def extract_btts(match_bets: list, outcomes: dict) -> int | str:
    try:
        target = None
        for bet in match_bets:
            name = str(bet.get("betTypeName") or bet.get("betTitle") or "").strip().lower()
            if name in ("les 2 équipes marquent", "les 2 equipes marquent"):
                target = bet
                break
        if not target:
            for bet in match_bets:
                name = str(bet.get("betTypeName") or "").strip().lower()
                if (
                    "marquent" in name
                    and "mi-temps" not in name
                    and "double chance" not in name
                    and "résultat" not in name
                    and ("les 2" in name or "btts" in name)
                ):
                    target = bet
                    break
        if not target:
            return UNAVAILABLE
        for oid in target.get("outcomes") or []:
            out = lookup(outcomes, oid)
            if not out:
                continue
            label = str(out.get("label") or "").lower()
            code = str(out.get("code") or "").lower()
            if "oui" in label or "yes" in code or code in ("oui", "o", "yes"):
                pct = _pct_int(out)
                if pct is not None:
                    return pct
    except Exception:
        pass
    return UNAVAILABLE


def extract_over_25(
    match_bets: list, outcomes: dict, odds: dict | None = None
) -> int | str:
    try:
        if odds:
            pm = extract_plus_moins_buts(match_bets, outcomes, odds)
            line = pm.get("2.5")
            if isinstance(line, dict) and isinstance(line.get("plus_prob"), int):
                return line["plus_prob"]
        for bet in match_bets:
            name = str(bet.get("betTypeName") or bet.get("betTitle") or "").strip().lower()
            if name != "nombre de buts":
                continue
            for oid in bet.get("outcomes") or []:
                out = lookup(outcomes, oid)
                if not out:
                    continue
                label = str(out.get("label") or "").lower().replace(",", ".")
                if "plus" not in label or "2.5" not in label:
                    continue
                pct = _pct_int(out)
                if pct is not None:
                    return pct
    except Exception:
        pass
    return UNAVAILABLE


def _bet_label(bet: dict) -> str:
    parts = [
        bet.get("betTypeName"),
        bet.get("betTitle"),
        bet.get("betFilterName"),
        bet.get("specialBetValue"),
    ]
    return " ".join(str(p) for p in parts if p).strip().lower()


def _parse_ou_side_line(label: str) -> tuple[str | None, str | None]:
    """Retourne ('plus'|'moins', '1.5'|'2.5'|'3.5') depuis un libellé d'issue."""
    try:
        lab = str(label or "").lower().replace(",", ".")
        m = re.search(r"(plus|moins)\s*(?:de\s*)?(\d+(?:\.\d+)?)", lab)
        if not m:
            return None, None
        side = m.group(1)
        line = m.group(2)
        if line in GOAL_LINES:
            return side, line
        for gl in GOAL_LINES:
            if line.startswith(gl):
                return side, gl
    except Exception:
        pass
    return None, None


def _is_goals_total_bet(name: str) -> bool:
    if "nombre de buts" in name:
        return True
    if "total" in name and "but" in name:
        return True
    if "plus/moins" in name and "but" in name:
        return True
    if "nb de buts" in name or "nb buts" in name:
        return True
    return False


def extract_plus_moins_buts(
    match_bets: list, outcomes: dict, odds: dict
) -> dict[str, dict | str]:
    """Seuils 1.5 / 2.5 / 3.5 — cotes et probas Plus / Moins si disponibles."""
    result: dict[str, dict | str] = {line: INDISPONIBLE for line in GOAL_LINES}
    try:
        buckets: dict[str, dict] = {line: {} for line in GOAL_LINES}
        for bet in match_bets:
            name = _bet_label(bet)
            if not _is_goals_total_bet(name):
                continue
            if "mi-temps" in name or "mi temps" in name or "1ère" in name or "1ere" in name:
                continue
            if "équipe" in name and "nombre de buts" not in name:
                continue
            for oid in bet.get("outcomes") or []:
                out = lookup(outcomes, oid)
                if not out:
                    continue
                side, line = _parse_ou_side_line(out.get("label") or "")
                if not side or not line:
                    continue
                entry = buckets[line]
                price = lookup_odd(odds, oid)
                if price is not None:
                    entry[f"{side}_cote"] = round(float(price), 2)
                pct = _pct_int(out)
                if pct is not None:
                    entry[f"{side}_prob"] = pct
        for line in GOAL_LINES:
            data = buckets[line]
            if data.get("plus_cote") is not None or data.get("moins_cote") is not None:
                result[line] = data
    except Exception:
        pass
    return result


def _is_buteur_match_market(name: str) -> bool:
    """Compat sniper : filtre strict via bet dict dans _collect_scorer_rows."""
    return False


def _is_buteur_mi_temps_market(name: str) -> bool:
    if "buteur" not in name and "buteurs" not in name:
        return False
    return any(
        x in name
        for x in (
            "mi-temps",
            "mi temps",
            "1ère",
            "1ere",
            "première mi",
            "premiere mi",
            " mi-tps",
            "1re mi",
        )
    )


def _is_buteur_double_market(name: str) -> bool:
    if "buteur" not in name and "buteurs" not in name:
        return False
    return any(
        x in name
        for x in (
            "doubl",
            "duo",
            "multiple",
            "2 joueurs",
            "2 joueur",
            "deux joueurs",
        )
    )


_SKIP_SCORER_LABELS = frozenset(
    {
        "oui",
        "non",
        "aucun",
        "aucun buteur",
        "no scorer",
        "sans buteur",
        "personne",
        "other",
        "autre",
    }
)


def _collect_scorer_rows(
    match_bets: list,
    outcomes: dict,
    odds: dict,
    matcher,
    limit: int = 5,
) -> list[dict]:
    """Buteur match : parser strict ; autres marchés buteur : label joueur filtré."""
    if matcher is _is_buteur_match_market:
        return _collect_buteur_rows(match_bets, outcomes, odds, limit)

    rows: list[dict] = []
    seen: set[str] = set()
    try:
        for bet in match_bets:
            if not matcher(_bet_label(bet)):
                continue
            for oid in bet.get("outcomes") or []:
                out = lookup(outcomes, oid)
                if not out:
                    continue
                joueur = _extract_scorer_player_name(out)
                if not joueur:
                    continue
                key = joueur.lower()
                if key in seen:
                    continue
                price = lookup_odd(odds, oid)
                if price is None or price > 50.0:
                    continue
                seen.add(key)
                rows.append({"joueur": joueur, "cote": round(float(price), 2)})
        rows.sort(key=lambda x: x["cote"])
    except Exception:
        pass
    return rows


def _top_scorers(
    match_bets: list,
    outcomes: dict,
    odds: dict,
    matcher,
    limit: int,
) -> list[dict] | str:
    try:
        rows = _collect_scorer_rows(match_bets, outcomes, odds, matcher, limit)
        if not rows:
            return INDISPONIBLE
        return rows[:limit]
    except Exception:
        return INDISPONIBLE


def default_marches_supplementaires() -> dict:
    return {
        "plus_moins_buts": {line: INDISPONIBLE for line in GOAL_LINES},
        "buteur_match": INDISPONIBLE,
        "buteur_mi_temps": INDISPONIBLE,
        "buteur_multiple": INDISPONIBLE,
    }


def extract_marches_supplementaires(
    match_bets: list, outcomes: dict, odds: dict
) -> dict:
    """Marchés détail Winamax structurés pour api_velora_premium.json."""
    try:
        return {
            "plus_moins_buts": extract_plus_moins_buts(
                match_bets, outcomes, odds
            ),
            "buteur_match": _top_scorers(
                match_bets,
                outcomes,
                odds,
                _is_buteur_match_market,
                5,
            ),
            "buteur_mi_temps": _top_scorers(
                match_bets,
                outcomes,
                odds,
                _is_buteur_mi_temps_market,
                3,
            ),
            "buteur_multiple": _top_scorers(
                match_bets,
                outcomes,
                odds,
                _is_buteur_double_market,
                3,
            ),
        }
    except Exception:
        return default_marches_supplementaires()


def _probas_1n2_suspectes(probs: dict | None) -> bool:
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


def _inject_poisson_model_scores(match: dict) -> dict:
    """Scores & probas modèle si le pipeline v2 ou Winamax n'en fournit pas."""
    updated = dict(match)
    free = dict(updated.get("free_analysis") or {})
    if isinstance(free.get("top_scores_modele"), list) and free["top_scores_modele"]:
        return updated

    cotes = free.get("cotes_1n2") or updated.get("cotes") or {}
    if not any(cotes.get(k) for k in ("1", "N", "2")):
        return updated

    from velora_engine.analysis.model_poisson import build_poisson_analysis
    from velora_engine.models import MarketsRaw

    intel = updated.get("velora_intel")
    poisson = build_poisson_analysis(cotes=cotes, intel=intel, markets=MarketsRaw())
    pick = _pronostic_pick_from_match(updated)
    scores = align_top_scores_for_pick(
        poisson.top_scores,
        pick,
        matrix=poisson.matrix,
        limit=5,
    )
    if not scores:
        return updated

    free["top_scores_modele"] = scores
    free.setdefault(
        "poisson_lambdas",
        {"home": poisson.lambda_home, "away": poisson.lambda_away},
    )
    if pick:
        free.setdefault("pronostic_1n2", pick)
    cur_probs = free.get("probabilites") or updated.get("probabilites")
    if _probas_1n2_suspectes(cur_probs):
        free["probabilites"] = poisson.probabilites_1n2
        updated["probabilites"] = poisson.probabilites_1n2
    updated["free_analysis"] = free
    updated["top_scores"] = scores
    return updated


def _pronostic_pick_from_match(match: dict) -> str:
    free = match.get("free_analysis") or {}
    pick = (
        match.get("velora_pick_1n2")
        or free.get("pronostic_1n2")
        or (free.get("primary_pick") or {}).get("pick")
    )
    return str(pick or "").strip()


def _align_display_scores_for_pick(match: dict) -> dict:
    """Scores affichés alignés sur le pronostic Velora 1N2."""
    updated = dict(match)
    pick = _pronostic_pick_from_match(updated)
    if pick not in ("1", "N", "2", "dc_1x", "dc_x2"):
        return updated

    free = updated.get("free_analysis") or {}
    model_scores = free.get("top_scores_modele")
    if isinstance(model_scores, list) and model_scores:
        updated["top_scores"] = model_scores[:5]
    elif isinstance(updated.get("top_scores"), list) and updated["top_scores"]:
        aligned = align_top_scores_for_pick(updated["top_scores"], pick, limit=5)
        if aligned:
            updated["top_scores"] = aligned

    exact = updated.get("score_exact")
    if isinstance(exact, list) and exact:
        aligned_exact = align_top_scores_for_pick(
            finalize_score_rows_probs(list(exact)),
            pick,
            limit=5,
        )
        if aligned_exact:
            updated["score_exact"] = aligned_exact

    for field in ("top_scores", "score_exact"):
        val = updated.get(field)
        if isinstance(val, list):
            updated[field] = finalize_score_rows_probs(list(val))
    return updated


def _apply_velora_engine(updated: dict, state: dict | None, page=None) -> dict:
    """Indice Velora = score pondéré historique ; pas de fallback cotes."""
    mid = updated.get("id_match")
    intel = (
        extract_intel_from_page(page, mid)
        if page is not None
        else extract_intel_from_state(state, mid)
    )
    if page is not None and state and isinstance(state, dict):
        state_intel = extract_intel_from_state(state, mid)
        if state_intel.get("has_form") and not intel.get("has_form"):
            intel = state_intel
        elif state_intel.get("h2h") and not intel.get("h2h"):
            intel = {**intel, "h2h": state_intel["h2h"]}

    if intel_stats_suffisantes(intel):
        return apply_statistical_velora_analysis(updated, intel)

    base = apply_velora_analysis(updated)
    return apply_non_calculable_velora(
        base,
        "Statistiques insuffisantes (forme / classement non extraites sur Winamax)",
    )


def enrich_from_state(match: dict, state: dict | None, page=None) -> dict:
    """Met à jour le match avec marchés détail + analyse statistique Velora."""
    updated = dict(match)
    updated["marches_supplementaires"] = default_marches_supplementaires()
    if is_match_live(updated) or is_match_live(
        {"status": updated.get("match_status")}
    ):
        updated = apply_non_calculable_velora(
            updated,
            "Match en direct — cotes annexes non utilisées",
        )
        return updated
    if not state or not isinstance(state, dict):
        if _top_scores_empty(updated.get("top_scores")):
            updated["top_scores"] = UNAVAILABLE
        if _btts_empty(updated.get("les_deux_marquent")):
            updated["les_deux_marquent"] = UNAVAILABLE
        if page is not None:
            updated = _apply_velora_engine(updated, None, page=page)
        return updated

    try:
        mid = updated.get("id_match")
        mbets = bets_for_match(state.get("bets") or {}, mid)
        outcomes = state.get("outcomes") or {}
        odds = state.get("odds") or {}

        updated["marches_supplementaires"] = extract_marches_supplementaires(
            mbets, outcomes, odds
        )

        if _top_scores_empty(updated.get("top_scores")):
            updated["top_scores"] = extract_score_exact(mbets, outcomes, odds)

        if _btts_empty(updated.get("les_deux_marquent")):
            updated["les_deux_marquent"] = extract_btts(mbets, outcomes)

        apply_annex_markets(updated, mbets, outcomes, odds)

        over = extract_over_25(mbets, outcomes, odds)
        if isinstance(over, int):
            probs = updated.get("probabilites") or {}
            updated["tendance_buts"] = tendance_buts(over, probs)
        elif over == UNAVAILABLE and not updated.get("tendance_buts"):
            updated["tendance_buts"] = updated.get("tendance_buts") or "Match Tactique"

        updated = _apply_velora_engine(updated, state, page=page)
        updated = ensure_match_scores_coherent(updated)
    except Exception:
        if _top_scores_empty(updated.get("top_scores")):
            updated["top_scores"] = UNAVAILABLE
        if _btts_empty(updated.get("les_deux_marquent")):
            updated["les_deux_marquent"] = UNAVAILABLE
        updated["marches_supplementaires"] = default_marches_supplementaires()

    return _sync_premium_analysis_from_marches(
        ensure_match_scores_coherent(updated)
    )


def _scorer_rows_from_value(val) -> list[dict]:
    if isinstance(val, list) and val:
        return [r for r in val if isinstance(r, dict) and r.get("joueur")]
    return []


def _sync_premium_analysis_from_marches(match: dict) -> dict:
    """Recopie buteurs / scores exacts enrichis dans premium_analysis (UI v2)."""
    updated = dict(match)
    ms = updated.get("marches_supplementaires") or {}
    prem = dict(updated.get("premium_analysis") or {})

    def _set_scorer_block(key: str, rows: list[dict], limit: int) -> None:
        block = dict(prem.get(key) or {})
        if rows and not block.get("top"):
            block["top"] = rows[:limit]
        prem[key] = block

    bm = _scorer_rows_from_value(ms.get("buteur_match"))
    if not bm:
        bm = _scorer_rows_from_value(updated.get("buteurs"))
    _set_scorer_block("buteur_match", bm, 5)

    mt = _scorer_rows_from_value(ms.get("buteur_mi_temps"))
    if not mt:
        mt = _scorer_rows_from_value(updated.get("buteurs_mi_temps"))
    _set_scorer_block("buteur_mi_temps", mt, 3)

    dbl = _scorer_rows_from_value(ms.get("buteur_multiple"))
    _set_scorer_block("buteur_double", dbl, 3)

    score_block = dict(prem.get("score_exact") or {})
    if not score_block.get("top3"):
        se = updated.get("score_exact")
        if isinstance(se, list) and se:
            score_block["top3"] = [
                s for s in se if isinstance(s, dict) and s.get("score")
            ][:3]
    prem["score_exact"] = score_block
    updated["premium_analysis"] = prem
    return updated


def _state_has_scorer_bets(state: dict, match_id) -> bool:
    mbets = bets_for_match(state.get("bets") or {}, match_id)
    return any(_is_scorer_market_bet(b) for b in mbets)


def _activate_scorer_markets(page) -> None:
    """Ouvre le filtre Buteurs (marchés lazy-loaded sur la page match)."""
    try:
        clicked = page.evaluate(
            """() => {
                const norm = (s) => String(s || '').trim().toLowerCase();
                const targets = ['buteurs', 'buteur'];
                const nodes = Array.from(
                    document.querySelectorAll('button,a,[role="tab"],[role="button"],span,div')
                );
                for (const t of targets) {
                    const el = nodes.find((e) => norm(e.textContent) === t);
                    if (el) {
                        el.click();
                        return t;
                    }
                }
                return null;
            }"""
        )
        if clicked:
            page.wait_for_timeout(1200)
    except Exception:
        pass


def ensure_scorer_markets_state(
    page, match_id, state: dict | None
) -> dict | None:
    if state and _state_has_scorer_bets(state, match_id):
        return state
    _activate_scorer_markets(page)
    try:
        page.wait_for_function(
            """(mid) => {
                const s = window.PRELOADED_STATE;
                if (!s || !s.bets) return false;
                for (const b of Object.values(s.bets)) {
                    if (!b || String(b.matchId) !== String(mid)) continue;
                    const fn = (b.betFilterName || '').toLowerCase();
                    if (fn === 'buteur' || fn === 'buteurs' || fn === 'marqueur') return true;
                    const fid = Number(b.betFilterId || 0);
                    if (fid === 26) return true;
                    const tn = (b.betTypeName || b.betTitle || '').toLowerCase();
                    if (tn.includes('buteur du match') || tn.includes('marqueur du match')) return true;
                }
                return false;
            }""",
            arg=str(match_id),
            timeout=12000,
        )
    except Exception:
        pass
    return fetch_preloaded_state(page) or state


def fetch_preloaded_state(page) -> dict | None:
    try:
        return page.evaluate(
            """() => {
                const s = window.PRELOADED_STATE;
                return (s && typeof s === 'object') ? s : null;
            }"""
        )
    except Exception:
        return None


def _has_velora_intel(match: dict) -> bool:
    vi = match.get("velora_intel") or {}
    return bool((vi.get("home_form") or {}).get("played"))


def select_sniper_batch(
    all_matches: list[dict], now: datetime | None = None
) -> list[dict]:
    """Matchs journée+nocturnes : priorité sans intel, puis les plus proches (limite SNIPER_LIMIT)."""
    now = now or datetime.now(tz=TZ_PARIS)
    in_window = [m for m in all_matches if is_within_sniper_window(m, now)]
    in_window.sort(
        key=lambda m: (
            0 if _has_velora_intel(m) else 1,
            0 if needs_enrichment(m) else 1,
            m.get("match_start_ts") or 2**62,
        )
    )
    return in_window[:SNIPER_LIMIT]


def load_matches() -> list[dict]:
    try:
        data = json.loads(IN_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            matchs = data.get("matchs")
            if isinstance(matchs, list):
                return matchs
        return []
    except Exception as e:
        print(f"[sniper] ECHEC lecture {IN_PATH.name}: {e}")
        return []


def _load_matchs_document() -> tuple[list[dict], dict | list | None]:
    try:
        data = json.loads(IN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return [], None
    if isinstance(data, list):
        return data, data
    if isinstance(data, dict) and isinstance(data.get("matchs"), list):
        return data["matchs"], data
    return [], data if isinstance(data, dict) else None


def _touch_matchs_document_meta(doc: dict, match_count: int) -> dict:
    """Horodate meta.generated_at pour que le front détecte les MAJ auto."""
    out = dict(doc)
    meta = dict(out.get("meta") or {})
    meta["generated_at"] = datetime.now(tz=TZ_PARIS).isoformat()
    meta["match_count"] = match_count
    out["meta"] = meta
    return out


def _write_outputs(all_matches: list[dict], updates: dict[str, dict]) -> list[dict]:
    """Écrit premium + fusionne l'enrichissement dans api_velora_matchs.json."""
    by_id = {str(m.get("id_match")): m for m in all_matches}
    by_id.update(updates)
    stamped: list[dict] = []
    for m in all_matches:
        rec = dict(by_id[str(m.get("id_match"))])
        rec.setdefault("marches_supplementaires", default_marches_supplementaires())
        stamped.append(rec)

    OUT_PATH.write_text(
        json.dumps(stamped, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _, doc = _load_matchs_document()
    if isinstance(doc, dict) and isinstance(doc.get("matchs"), list):
        doc = _touch_matchs_document_meta(dict(doc), len(stamped))
        doc["matchs"] = stamped
        IN_PATH.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        IN_PATH.write_text(
            json.dumps(stamped, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return stamped


def main() -> None:
    print("[sniper] Chargement api_velora_matchs.json...")
    all_matches = load_matches()
    if not all_matches:
        return

    now = datetime.now(tz=TZ_PARIS)
    in_window = [m for m in all_matches if is_within_sniper_window(m, now)]
    out_of_window = [m for m in all_matches if not is_within_sniper_window(m, now)]
    batch = select_sniper_batch(all_matches, now)
    updates: dict[str, dict] = {}

    for match in out_of_window:
        mid = match.get("id_match")
        home = match.get("equipe_domicile", "?")
        away = match.get("equipe_exterieur", "?")
        delay = describe_skip_delay(match, now)
        print(
            f"[sniper] Match {home} - {away} ignoré ({delay}) -> Gain de temps"
        )
        updates[str(mid)] = stamp_skipped_match(match)

    print(
        f"[sniper] {len(in_window)} match(s) dans {SNIPER_WINDOW_HOURS}h — "
        f"{len(out_of_window)} hors fenêtre (sans HTTP), "
        f"traitement HTTP de {len(batch)} (limite {SNIPER_LIMIT})."
    )

    if not batch:
        stamped = _write_outputs(all_matches, updates)
        skipped_n = len(updates)
        if skipped_n:
            print(
                f"[sniper] {skipped_n} match(s) marqué(s) Indisponible (hors fenêtre). "
                f"Copie -> {OUT_PATH.name}"
            )
        else:
            print(f"[sniper] Rien à enrichir. Copie -> {OUT_PATH.name}")
        return

    with sync_playwright() as p:
        browser, context, page = _open_playwright_session(p)
        if proxy_interactive_enabled() and os.environ.get("VELORA_PROXY_URL", "").strip():
            first_mid = batch[0].get("id_match")
            wait_for_proxy_authentication(
                page,
                MATCH_URL.format(id_match=first_mid),
                label="sniper",
            )

        for i, match in enumerate(batch, 1):
            mid = match.get("id_match")
            home = match.get("equipe_domicile", "?")
            away = match.get("equipe_exterieur", "?")
            url = MATCH_URL.format(id_match=mid)
            print(f"[sniper] ({i}/{len(batch)}) {home} - {away} -> {mid}")

            state = None
            try:
                page.goto(url, timeout=90_000, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=30_000)
                except Exception:
                    pass
                state = fetch_preloaded_state(page)
                if page is not None:
                    state = ensure_scorer_markets_state(page, mid, state)
            except Exception as e:
                print(f"  ATTENTION navigation: {e}")

            enriched = enrich_from_state(match, state, page=page)
            updates[str(mid)] = enriched

            ts = enriched.get("top_scores")
            btts = enriched.get("les_deux_marquent")
            ms = enriched.get("marches_supplementaires") or {}
            vi = enriched.get("velora_intel") or {}
            hf = vi.get("home_form") or {}
            af = vi.get("away_form") or {}
            print(f"  top_scores: {ts if isinstance(ts, str) else len(ts) if isinstance(ts, list) else ts}")
            print(f"  les_deux_marquent: {btts}")
            print(f"  forme: {hf.get('raw', '—')} vs {af.get('raw', '—')}")
            print(f"  indice_velora: {enriched.get('indice_velora')}")
            print(f"  tendance_buts: {enriched.get('tendance_buts')}")
            print(f"  value_bet_type: {enriched.get('value_bet_type', '—')}")
            print(f"  conseil: {enriched.get('conseil')}")
            print(f"  plus_moins_buts: {ms.get('plus_moins_buts')}")
            bm = ms.get("buteur_match")
            print(
                f"  buteur_match: "
                f"{len(bm) if isinstance(bm, list) else bm}"
            )

            if i < len(batch):
                time.sleep(PAUSE_SECONDS)

        context.close()
        if browser is not None:
            browser.close()

    stamped = _write_outputs(all_matches, updates)
    http_n = len(updates) - len(out_of_window)
    print(
        f"[sniper] SUCCES — {http_n} match(s) enrichi(s) via HTTP, "
        f"{len(out_of_window)} ignoré(s) (hors fenêtre) -> {OUT_PATH.name} + {IN_PATH.name}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
