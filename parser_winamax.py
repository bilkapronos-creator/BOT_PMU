"""
Parse dump_winamax_html.json — Velora Engine (cotes, probas, value, analyses).
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ_PARIS = ZoneInfo("Europe/Paris")

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

DUMP = Path(__file__).resolve().parent / "dump_winamax_html.json"
OUT_API = Path(__file__).resolve().parent / "api_velora_matchs.json"
FOOTBALL_SPORT_ID = 1
NOT_AVAILABLE = "Compos non disponibles"
DISPLAY_LIMIT = 15
VALUE_THRESHOLD = 1.05
MAX_CONSEIL_LEN = 40
MAX_CONSEIL_EXPERT = 56
VALUE_PREFIX = "🔥 Value Bet Détecté :"
SCORE_EXACT_COTE_MIN = 7.50
BTTS_PLUS25_MAX = 1.85
BTTS_1N2_GAP_MAX = 1.00
BTTS_OUI_VALUE_MIN = 2.00
MATCH_FERME_MOIN25_MAX = 1.65
FAVORI_1N2_STRONG = 1.80
FAVORI_BUTEUR_MAX = 1.70
BUTEUR_VALUE_COTE_MIN = 2.50
OVER25_VALUE_MIN = 1.82
OVER25_VALUE_MAX = 2.12
OVER25_OFFENSIF_MAX = 1.88


def split_title(title: str) -> tuple[str | None, str | None]:
    for sep in (" - ", " – ", " vs "):
        if sep in title:
            a, b = title.split(sep, 1)
            return a.strip(), b.strip()
    return None, None


def get_teams(match: dict) -> tuple[str, str]:
    try:
        home = match.get("competitor1Name")
        away = match.get("competitor2Name")
        if home and away:
            return str(home), str(away)
    except Exception:
        pass
    home, away = split_title(str(match.get("title") or ""))
    return home or "?", away or "?"


def lookup(mapping: dict, key) -> dict | None:
    try:
        val = mapping.get(str(key)) or mapping.get(key)
        return val if isinstance(val, dict) else None
    except Exception:
        return None


def lookup_odd(odds: dict, outcome_id) -> float | None:
    try:
        val = odds.get(str(outcome_id)) or odds.get(outcome_id)
        if isinstance(val, dict):
            val = val.get("odds") or val.get("price")
        return float(val) if val is not None else None
    except Exception:
        return None


def bets_for_match(bets: dict, match_id) -> list[dict]:
    found = []
    try:
        mid = int(match_id)
    except Exception:
        mid = match_id
    for bet in bets.values():
        if not isinstance(bet, dict):
            continue
        try:
            if bet.get("matchId") == mid or str(bet.get("matchId")) == str(match_id):
                found.append(bet)
        except Exception:
            continue
    return found


def find_main_bet(bets: list[dict], main_bet_id) -> dict | None:
    try:
        if main_bet_id:
            for bet in bets:
                if bet.get("betId") == main_bet_id or str(bet.get("betId")) == str(main_bet_id):
                    return bet
    except Exception:
        pass
    for bet in bets:
        try:
            tpl = str(bet.get("template", "")).lower()
            name = str(bet.get("betTypeName") or bet.get("betTitle") or "").lower()
            if tpl in ("3way", "1x2") or "résultat" in name or "resultat" in name:
                return bet
        except Exception:
            continue
    return None


def extract_1n2_from_bet(bet: dict, outcomes: dict, odds: dict) -> tuple[dict, dict]:
    """Retourne (cotes {1,N,2}, probas brutes par code)."""
    cotes: dict[str, float | None] = {"1": None, "N": None, "2": None}
    raw_pct: dict[str, float | None] = {"1": None, "N": None, "2": None}
    try:
        for oid in bet.get("outcomes") or []:
            out = lookup(outcomes, oid)
            if not out:
                continue
            code = str(out.get("code", "")).lower()
            price = lookup_odd(odds, oid)
            pct = out.get("percentDistribution")
            if pct is not None:
                try:
                    raw_pct_key = None
                    if code == "1":
                        raw_pct_key = "1"
                    elif code in ("x", "n"):
                        raw_pct_key = "N"
                    elif code == "2":
                        raw_pct_key = "2"
                    if raw_pct_key:
                        val = float(pct)
                        if 0 < val <= 1:
                            val *= 100
                        raw_pct[raw_pct_key] = val
                except Exception:
                    pass
            if code == "1":
                cotes["1"] = price
            elif code in ("x", "n"):
                cotes["N"] = price
            elif code == "2":
                cotes["2"] = price
    except Exception:
        pass
    return cotes, raw_pct


def implied_probabilities(cotes: dict[str, float | None]) -> dict[str, int]:
    inv: dict[str, float] = {}
    try:
        for key in ("1", "N", "2"):
            c = cotes.get(key)
            if c and c > 0:
                inv[key] = 1.0 / c
        total = sum(inv.values())
        if total <= 0:
            return {"1": 0, "N": 0, "2": 0}
        return {k: int(round(100 * v / total)) for k, v in inv.items()}
    except Exception:
        return {"1": 0, "N": 0, "2": 0}


def _probas_winamax_plates(probs: dict[str, int]) -> bool:
    """Winamax renvoie parfois ~33/33/33 sans signal réel — on préfère les cotes."""
    try:
        vals = [int(probs.get(k) or 0) for k in ("1", "N", "2")]
        if sum(vals) < 90:
            return False
        return max(vals) - min(vals) <= 8 and min(vals) >= 28
    except (TypeError, ValueError):
        return False


def finalize_probabilities(raw_pct: dict, cotes: dict) -> dict[str, int]:
    probs: dict[str, int] = {}
    try:
        for key in ("1", "N", "2"):
            v = raw_pct.get(key)
            if v is not None:
                probs[key] = int(round(float(v)))
            else:
                probs[key] = 0
        total = sum(probs.values())
        if total == 0:
            return implied_probabilities(cotes)
        if _probas_winamax_plates(probs):
            return implied_probabilities(cotes)
        if total != 100:
            factor = 100 / total
            probs = {k: int(round(probs[k] * factor)) for k in probs}
            diff = 100 - sum(probs.values())
            if diff and probs:
                kmax = max(probs, key=probs.get)
                probs[kmax] += diff
        return probs
    except Exception:
        return implied_probabilities(cotes)


def calc_value_metrics(probs: dict[str, int], cotes: dict[str, float | None]) -> tuple[int, str | None]:
    """Indice Velora 1-5 et meilleure issue value."""
    try:
        best_idx = 0.0
        best_key = None
        for key in ("1", "N", "2"):
            p = probs.get(key, 0) / 100.0
            c = cotes.get(key)
            if c and p > 0:
                indice = p * c
                if indice > best_idx:
                    best_idx = indice
                    best_key = key
        if best_idx < 1.0:
            stars = 1
        elif best_idx < 1.02:
            stars = 2
        elif best_idx < VALUE_THRESHOLD:
            stars = 3
        elif best_idx < 1.15:
            stars = 4
        else:
            stars = 5
        return stars, best_key if best_idx >= VALUE_THRESHOLD else None
    except Exception:
        return 1, None


def _truncate_conseil(text: str, limit: int = MAX_CONSEIL_LEN) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def normalize_score_tuple(label: str) -> tuple[int, int] | None:
    try:
        nums = [int(x) for x in re.findall(r"\d+", str(label or ""))]
        if len(nums) >= 2:
            return nums[0], nums[1]
    except Exception:
        pass
    return None


def _safe_float(val) -> float | None:
    try:
        if val is None:
            return None
        return float(val)
    except Exception:
        return None


def _favori_1n2(cotes: dict) -> tuple[str | None, float | None]:
    try:
        c1 = _safe_float(cotes.get("1"))
        c2 = _safe_float(cotes.get("2"))
        if c1 is None or c2 is None:
            return None, None
        if c1 <= c2:
            return "1", c1
        return "2", c2
    except Exception:
        return None, None


def _ligne_ou_cote(marches: dict | None, line: str, side: str) -> float | None:
    try:
        pm = (marches or {}).get("plus_moins_buts") or {}
        row = pm.get(line)
        if not isinstance(row, dict):
            return None
        return _safe_float(row.get(f"{side}_cote"))
    except Exception:
        return None


def _append_score_row(rows: list, out: dict, oid, odds: dict | None) -> None:
    label = str(out.get("label") or "").strip()
    prob = out.get("percentDistribution") or out.get("probability")
    if prob is None or not label:
        return
    try:
        p = float(prob)
        if 0 < p <= 1:
            p = int(round(p * 100))
        else:
            p = int(round(p))
    except Exception:
        return
    item = {"score": label, "prob": p}
    if odds:
        price = lookup_odd(odds, oid)
        if price is not None:
            item["cote"] = round(float(price), 2)
    rows.append(item)


def _match_1n2_serre(cotes: dict) -> bool:
    c1 = _safe_float(cotes.get("1"))
    c2 = _safe_float(cotes.get("2"))
    if c1 is None or c2 is None:
        return False
    return abs(c1 - c2) <= BTTS_1N2_GAP_MAX


def _buteurs_liste(record: dict) -> list[dict]:
    raw = record.get("buteurs")
    if isinstance(raw, list) and raw:
        return [b for b in raw if isinstance(b, dict)]
    ms = record.get("marches_supplementaires") or {}
    bm = ms.get("buteur_match")
    return bm if isinstance(bm, list) else []


def _scores_exact_liste(record: dict) -> list[dict]:
    raw = record.get("score_exact")
    if isinstance(raw, list) and raw:
        return [s for s in raw if isinstance(s, dict) and s.get("score")]
    top = record.get("top_scores")
    if not isinstance(top, list):
        return []
    rows = [s for s in top if isinstance(s, dict) and s.get("score")]
    rows.sort(key=lambda x: _safe_float(x.get("cote")) or 999)
    return rows


def _ou25_cotes(record: dict) -> dict[str, float | None]:
    ou = record.get("over_under_25")
    if isinstance(ou, dict):
        return {
            "plus": _safe_float(ou.get("plus")),
            "moins": _safe_float(ou.get("moins")),
        }
    ms = record.get("marches_supplementaires") or {}
    pm = (ms.get("plus_moins_buts") or {}).get("2.5")
    if isinstance(pm, dict):
        return {
            "plus": _safe_float(pm.get("plus_cote")),
            "moins": _safe_float(pm.get("moins_cote")),
        }
    return {"plus": None, "moins": None}


def _btts_oui_cote(record: dict) -> float | None:
    btts = record.get("btts")
    if isinstance(btts, dict):
        return _safe_float(btts.get("oui"))
    return None


def _expert_opportunite(
    kind: str,
    detail: str,
    stars: int,
    rentability: float,
    priority: int,
    conseil: str | None = None,
) -> dict:
    msg = conseil or detail
    return {
        "priority": priority,
        "stars": stars,
        "rentability": rentability,
        "conseil": _truncate_conseil(msg, MAX_CONSEIL_EXPERT),
        "kind": kind,
        "detail": detail,
    }


def detect_value_score_exact(record: dict) -> dict | None:
    """Value bet score exact (priorité max)."""
    try:
        cotes = record.get("cotes") or {}
        fav_side, fav_odd = _favori_1n2(cotes)
        if fav_side is None or fav_odd is None or fav_odd >= FAVORI_1N2_STRONG:
            return None

        ou = _ou25_cotes(record)
        moins_25 = ou.get("moins")
        if moins_25 is None:
            ms = record.get("marches_supplementaires") or {}
            moins_25 = _ligne_ou_cote(ms, "2.5", "moins")
        if moins_25 is None or moins_25 >= MATCH_FERME_MOIN25_MAX:
            return None

        expected = {(1, 0), (2, 0)} if fav_side == "1" else {(0, 1), (0, 2)}
        best: tuple[float, str] | None = None
        for row in _scores_exact_liste(record):
            tpl = normalize_score_tuple(row.get("score"))
            if tpl not in expected:
                continue
            cote_sc = _safe_float(row.get("cote"))
            if cote_sc is None or cote_sc < SCORE_EXACT_COTE_MIN:
                continue
            label = str(row.get("score") or "").strip()
            if best is None or cote_sc > best[0]:
                best = (cote_sc, label)

        if not best:
            return None

        cote_sc, score_lbl = best
        score_short = score_lbl.replace(" - ", "-").replace(" – ", "-")
        detail = f"Score exact : {score_short} @ {cote_sc:.2f}"
        return _expert_opportunite(
            "score_exact",
            detail,
            5,
            cote_sc,
            5,
            f"🎯 Value Bet Score Exact : {score_short} @ {cote_sc:.2f}",
        )
    except Exception:
        return None


def detect_value_btts_expert(record: dict) -> dict | None:
    """Value bet BTTS (match serré + cote Oui généreuse)."""
    try:
        cotes = record.get("cotes") or {}
        if not _match_1n2_serre(cotes):
            return None

        oui_cote = _btts_oui_cote(record)
        if oui_cote is not None and oui_cote >= BTTS_OUI_VALUE_MIN:
            detail = f"BTTS — Oui @ {oui_cote:.2f}"
            stars = 5 if oui_cote >= 2.15 else 4
            return _expert_opportunite("btts", detail, stars, oui_cote, 3, f"🔥 Value Bet : {detail}")

        ms = record.get("marches_supplementaires") or {}
        plus_25 = _ou25_cotes(record).get("plus") or _ligne_ou_cote(ms, "2.5", "plus")
        if plus_25 is None or plus_25 > BTTS_PLUS25_MAX:
            return None

        btts_pct = record.get("les_deux_marquent")
        ultra = isinstance(btts_pct, (int, float)) and int(btts_pct) >= 52
        if not ultra and plus_25 > 1.72:
            return None
        if oui_cote is None or oui_cote < 1.90:
            return None

        detail = f"BTTS — Oui @ {oui_cote:.2f}"
        rent = oui_cote
        return _expert_opportunite(
            "btts",
            detail,
            5,
            rent,
            2,
            f"🔥 Value Bet : Les 2 équipes marquent @ {oui_cote:.2f}",
        )
    except Exception:
        return None


def detect_value_buteur(record: dict) -> dict | None:
    """Value bet buteur (favori fort + cote joueur généreuse)."""
    try:
        cotes = record.get("cotes") or {}
        _, fav_odd = _favori_1n2(cotes)
        if fav_odd is None or fav_odd >= FAVORI_BUTEUR_MAX:
            return None

        buteurs = _buteurs_liste(record)
        if not buteurs:
            return None

        top = buteurs[0]
        nom = str(top.get("joueur") or "").strip()
        cote_j = _safe_float(top.get("cote"))
        if not nom or cote_j is None or cote_j < BUTEUR_VALUE_COTE_MIN:
            return None

        stars = 5 if cote_j >= 3.0 else 4
        detail = f"Buteur : {nom} @ {cote_j:.2f}"
        return _expert_opportunite(
            "buteur",
            detail,
            stars,
            cote_j,
            4,
            f"⚽ Value Bet Buteur : {nom} @ {cote_j:.2f}",
        )
    except Exception:
        return None


def detect_value_over_25(record: dict) -> dict | None:
    """Value bet Over 2.5 (match offensif ou cote haute sur match serré)."""
    try:
        ou = _ou25_cotes(record)
        plus = ou.get("plus")
        if plus is None:
            return None

        cotes = record.get("cotes") or {}
        tight = _match_1n2_serre(cotes)
        offensive = record.get("tendance_buts") == "Match Offensif"

        hit = False
        if offensive and plus <= OVER25_OFFENSIF_MAX:
            hit = True
        elif offensive and OVER25_VALUE_MIN <= plus <= OVER25_VALUE_MAX:
            hit = True
        elif tight and plus >= BTTS_OUI_VALUE_MIN:
            hit = True

        if not hit:
            return None

        stars = 5 if plus >= 2.0 or plus <= 1.85 else 4
        detail = f"Over 2.5 buts @ {plus:.2f}"
        return _expert_opportunite(
            "over_25",
            detail,
            stars,
            plus,
            2,
            f"📊 Value Bet : {detail}",
        )
    except Exception:
        return None


def pick_expert_value_bet(record: dict) -> dict | None:
    """Score exact > Buteur > BTTS > Over 2.5."""
    try:
        candidates = []
        for detector in (
            detect_value_score_exact,
            detect_value_buteur,
            detect_value_btts_expert,
            detect_value_over_25,
        ):
            hit = detector(record)
            if hit:
                candidates.append(hit)
        if not candidates:
            return None
        return max(candidates, key=lambda x: x.get("priority", 0))
    except Exception:
        return None


def _clear_opportunite(record: dict) -> None:
    for key in ("opportunite_type", "opportunite_detail", "is_opportunite"):
        record.pop(key, None)


def _set_opportunite_expert(record: dict, expert: dict) -> None:
    kind = str(expert.get("kind") or "").strip()
    record["opportunite_type"] = kind
    record["opportunite_detail"] = expert.get("detail") or expert.get("conseil") or ""
    record["is_opportunite"] = True


def apply_velora_analysis(record: dict) -> dict:
    """
    Conseils / opportunités marché (value bets) sans indice basé sur les cotes.
    L'indice Velora reste « Non calculable » tant que le sniper n'a pas les stats historiques.
    """
    from velora_intel import (  # noqa: PLC0415
        INDICE_NON_CALCULABLE,
        LABEL_NON_CALCULABLE,
        apply_non_calculable_velora,
    )

    try:
        record = apply_non_calculable_velora(record)
        expert = pick_expert_value_bet(record)
        if expert:
            record["conseil"] = expert["conseil"]
            record["value_bet_type"] = expert.get("kind")
            _set_opportunite_expert(record, expert)
            return record

        probs = record.get("probabilites") or {}
        cotes = record.get("cotes") or {}
        tendance = record.get("tendance_buts")
        _, value_key = calc_value_metrics(probs, cotes)
        record["conseil"] = build_conseil(probs, cotes, tendance, value_key)
        record.pop("value_bet_type", None)
        _clear_opportunite(record)
        record["indice_velora"] = INDICE_NON_CALCULABLE
        record["indice_velora_label"] = LABEL_NON_CALCULABLE
    except Exception:
        record = apply_non_calculable_velora(
            record, "Analyse indisponible — statistiques non extraites"
        )
        _clear_opportunite(record)
    return record


def build_conseil(
    probs: dict[str, int],
    cotes: dict[str, float | None],
    tendance_buts: str | None = None,
    value_key: str | None = None,
) -> str:
    """Conseil court (≤40 car.) avec value bet, double chance et tendance buts."""
    try:
        p1 = int(probs.get("1", 0) or 0)
        pn = int(probs.get("N", 0) or 0)
        p2 = int(probs.get("2", 0) or 0)
        offensive = tendance_buts == "Match Offensif"

        value_hits: list[tuple[float, str, str]] = []
        for key, label in (("1", "Dom"), ("N", "Nul"), ("2", "Ext")):
            c = cotes.get(key)
            p = (probs.get(key, 0) or 0) / 100.0
            if c and p > 0 and p * float(c) > VALUE_THRESHOLD:
                value_hits.append((p * float(c), key, label))
        value_hits.sort(reverse=True)

        fav_prob = max(p1, p2)
        fav_side = "1" if p1 >= p2 else "2"
        gap_fav_nul = fav_prob - pn
        dc_tight = 0 <= gap_fav_nul < 10

        if value_hits:
            _, vkey, vlabel = value_hits[0]
            if dc_tight:
                body = "DC 1X" if fav_side == "1" else "DC X2"
            else:
                body = vlabel
            return _truncate_conseil(f"{VALUE_PREFIX} {body}")

        if dc_tight:
            dc = "DC 1X" if fav_side == "1" else "DC X2"
            return _truncate_conseil(f"Double Chance {dc}")

        if offensive:
            return _truncate_conseil("Privilégier le +2.5 buts")

        if p1 >= 55:
            return _truncate_conseil("Favori domicile")
        if p2 >= 55:
            return _truncate_conseil("Favori extérieur")
        if pn >= 32:
            return _truncate_conseil("Match serré, nul possible")
        if fav_side == "1":
            return _truncate_conseil("Léger edge domicile")
        if fav_side == "2":
            return _truncate_conseil("Léger edge extérieur")
        return _truncate_conseil("Match équilibré")
    except Exception:
        return _truncate_conseil("Analyse indisponible")


def extract_top_scores(
    match_bets: list, outcomes: dict, odds: dict | None = None
) -> list[dict]:
    try:
        for bet in match_bets:
            name = str(bet.get("betTypeName") or bet.get("betTitle") or "").lower()
            if "score correct" not in name and "score exact" not in name:
                continue
            if "multichance" in name or "mi-temps" in name or "mi temps" in name:
                continue
            rows = []
            for oid in bet.get("outcomes") or []:
                out = lookup(outcomes, oid)
                if not out:
                    continue
                _append_score_row(rows, out, oid, odds)
            if rows:
                rows.sort(key=lambda x: x.get("prob", 0), reverse=True)
                return rows[:3]
    except Exception:
        pass
    return []


def extract_over_25_prob(match_bets: list, outcomes: dict) -> int | None:
    try:
        for bet in match_bets:
            name = str(bet.get("betTypeName") or bet.get("betTitle") or "").lower()
            sbv = str(bet.get("specialBetValue") or "").lower()
            if "total" not in name and "but" not in name:
                continue
            for oid in bet.get("outcomes") or []:
                out = lookup(outcomes, oid)
                if not out:
                    continue
                label = str(out.get("label") or "").lower()
                if "plus" not in label:
                    continue
                if "2.5" in label or "2,5" in label or "2.5" in sbv or "2,5" in sbv:
                    prob = out.get("percentDistribution") or out.get("probability")
                    if prob is None:
                        continue
                    p = float(prob)
                    if 0 < p <= 1:
                        return int(round(p * 100))
                    return int(round(p))
    except Exception:
        pass
    return None


OU25_MAX_COTE_SANE = float(os.environ.get("VELORA_OU25_MAX_COTE", "5.0"))
SCORER_BET_FILTER_NAMES = frozenset(
    {"buteur", "buteurs", "marqueur", "marqueurs"}
)
SCORER_BET_FORBIDDEN = (
    "double chance",
    "résultat",
    "resultat",
    "1n2",
    "3way",
    "mi-temps",
    "mi temps",
    "remplaçant",
    "remplacant",
    "mt et",
    "combiné",
    "combo",
    "gagne 2 mt",
    "meilleur marqueur et",
    "équipe 1",
    "équipe 2",
    "issue du match",
    "qualification",
)
OUTCOME_1N2_CODES = frozenset({"1", "2", "x", "n", "draw", "home", "away"})
OUTCOME_1N2_LABELS = frozenset(
    {
        "match nul",
        "nul",
        "égalité",
        "egalite",
        "draw",
        "domicile",
        "extérieur",
        "exterieur",
        "home",
        "away",
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

MATCH_LIVE_STATUSES = frozenset({"LIVE", "RUNNING", "INPLAY", "IN_PLAY"})


def is_match_live(raw_match: dict) -> bool:
    """True si le match est en cours (cotes O/U et annexes non fiables)."""
    if not isinstance(raw_match, dict):
        return False
    status = str(raw_match.get("status") or "").strip().upper().replace(" ", "_")
    return status in MATCH_LIVE_STATUSES


def _sanitize_ou_cote(price: float | None, max_cote: float | None = None) -> float | None:
    """Ignore les cotes O/U aberrantes (souvent match LIVE)."""
    if price is None:
        return None
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    limit = max_cote if max_cote is not None else OU25_MAX_COTE_SANE
    if p > limit or p < 1.01:
        return None
    return round(p, 2)


GOAL_LINES = ("1.5", "2.5", "3.5")
_SKIP_SCORER_LABELS = OUTCOME_1N2_LABELS


def _bet_label(bet: dict) -> str:
    parts = [
        bet.get("betTypeName"),
        bet.get("betTitle"),
        bet.get("betFilterName"),
        bet.get("specialBetValue"),
    ]
    return " ".join(str(p) for p in parts if p).strip().lower()


def _outcome_pct(out: dict) -> int | None:
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


def _parse_ou_side_line(label: str) -> tuple[str | None, str | None]:
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


def _is_btts_bet(name: str) -> bool:
    return (
        ("deux" in name and "marquent" in name)
        or "btts" in name
        or ("les 2" in name and "marquent" in name)
        or name in ("les 2 équipes marquent", "les 2 equipes marquent")
    )


def _is_buteur_match_market(name: str) -> bool:
    """Compat : délègue au filtre pari buteur/marqueur strict."""
    return False


def _is_scorer_market_bet(bet: dict) -> bool:
    """
    Pari buteur Winamax : filtre « Buteur » / « Marqueur » (betFilterId 26),
    pas les combinés 1N2 / double chance qui contiennent « buteur » dans le titre.
    """
    if not isinstance(bet, dict):
        return False
    filter_name = str(bet.get("betFilterName") or "").strip().lower()
    type_name = str(bet.get("betTypeName") or bet.get("betTitle") or "").strip().lower()
    template = str(bet.get("template") or "").strip().lower()
    blob = f"{filter_name} {type_name} {template}"

    if any(x in blob for x in SCORER_BET_FORBIDDEN):
        return False
    if any(x in blob for x in ("doubl", "duo", "multiple", "2 joueurs", "2 joueur", "triple")):
        return False

    if filter_name in SCORER_BET_FILTER_NAMES:
        return True

    if any(t in template for t in ("goalscorer", "scorer", "anytimegoalscorer", "anytime_scorer")):
        return True

    if ("buteur du match" in type_name or "marqueur du match" in type_name) and (
        "buteur" in type_name or "marqueur" in type_name
    ):
        return True

    return False


def _outcome_is_1n2_or_generic(out: dict) -> bool:
    """Exclut les issues 1N2 (ex. « Match nul ») des listes de buteurs."""
    code = str(out.get("code") or "").strip().lower()
    if code in OUTCOME_1N2_CODES:
        return True
    label = str(out.get("label") or out.get("name") or "").strip().lower()
    if not label:
        return True
    if label in OUTCOME_1N2_LABELS:
        return True
    if label in ("1", "2", "n", "x"):
        return True
    if "match nul" in label or label.startswith("plus de") or label.startswith("moins de"):
        return True
    if re.match(r"^\d+(\.\d+)?$", label):
        return True
    return False


def _extract_scorer_player_name(out: dict) -> str | None:
    """Nom joueur : player.name, playerName, ou label outcome (hors 1N2)."""
    if not isinstance(out, dict):
        return None

    player = out.get("player")
    if isinstance(player, dict):
        name = player.get("name") or player.get("fullName") or player.get("displayName")
        if not name:
            first = str(player.get("firstName") or player.get("firstname") or "").strip()
            last = str(player.get("lastName") or player.get("lastname") or "").strip()
            name = f"{first} {last}".strip()
        if name and not _outcome_is_1n2_or_generic({"label": name, "code": ""}):
            return str(name).strip()

    for key in (
        "playerName",
        "participantName",
        "scorerName",
        "competitorName",
        "shortName",
    ):
        val = out.get(key)
        if isinstance(val, str) and len(val.strip()) > 2:
            if not _outcome_is_1n2_or_generic({"label": val, "code": out.get("code")}):
                return val.strip()

    if _outcome_is_1n2_or_generic(out):
        return None

    label = str(out.get("label") or out.get("name") or "").strip()
    if len(label) < 3 or label.lower() in _SKIP_SCORER_LABELS:
        return None
    if len(label) <= 2 and label.isdigit():
        return None
    return label


def _is_score_exact_bet(name: str) -> bool:
    if "multichance" in name or "mi-temps" in name or "mi temps" in name:
        return False
    return "score correct" in name or "score exact" in name


def _btts_outcome_side(out: dict) -> str | None:
    label = str(out.get("label") or "").lower()
    code = str(out.get("code") or "").lower()
    if "oui" in label or code in ("yes", "oui", "o"):
        return "oui"
    if "non" in label or code in ("no", "non", "n"):
        return "non"
    return None


def extract_btts_cotes(
    match_bets: list, outcomes: dict, odds: dict | None = None
) -> dict[str, float | None] | None:
    try:
        for bet in match_bets:
            name = _bet_label(bet)
            if not _is_btts_bet(name):
                continue
            if "mi-temps" in name or "double chance" in name or "résultat" in name:
                continue
            row: dict[str, float | None] = {"oui": None, "non": None}
            for oid in bet.get("outcomes") or []:
                out = lookup(outcomes, oid)
                if not out:
                    continue
                side = _btts_outcome_side(out)
                if not side:
                    continue
                price = lookup_odd(odds or {}, oid)
                if price is not None:
                    row[side] = round(float(price), 2)
            if row["oui"] is not None or row["non"] is not None:
                return row
    except Exception:
        pass
    return None


def extract_plus_moins_buts(
    match_bets: list, outcomes: dict, odds: dict | None = None
) -> dict[str, dict | None]:
    result: dict[str, dict | None] = {line: None for line in GOAL_LINES}
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
                price = _sanitize_ou_cote(lookup_odd(odds or {}, oid))
                if price is not None:
                    entry[f"{side}_cote"] = price
                pct = _outcome_pct(out)
                if pct is not None:
                    entry[f"{side}_prob"] = pct
        for line in GOAL_LINES:
            data = buckets[line]
            if data.get("plus_cote") is not None or data.get("moins_cote") is not None:
                result[line] = data
    except Exception:
        pass
    return result


def extract_over_under_25_cotes(
    match_bets: list, outcomes: dict, odds: dict | None = None
) -> dict[str, float | None] | None:
    try:
        pm = extract_plus_moins_buts(match_bets, outcomes, odds).get("2.5")
        if not isinstance(pm, dict):
            return None
        plus = _safe_float(pm.get("plus_cote"))
        moins = _safe_float(pm.get("moins_cote"))
        if plus is None and moins is None:
            return None
        return {
            "plus": round(plus, 2) if plus is not None else None,
            "moins": round(moins, 2) if moins is not None else None,
        }
    except Exception:
        return None


def _collect_buteur_rows(
    match_bets: list, outcomes: dict, odds: dict | None, limit: int = 4
) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    try:
        scorer_bets = [b for b in match_bets if _is_scorer_market_bet(b)]
        if not scorer_bets:
            return []

        for bet in scorer_bets:
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
                price = lookup_odd(odds or {}, oid)
                if price is None or price > 50.0:
                    continue
                seen.add(key)
                rows.append({"joueur": joueur, "cote": round(float(price), 2)})

        rows.sort(key=lambda x: x["cote"])
        return rows[:limit]
    except Exception:
        return []


def extract_buteurs_annexe(
    match_bets: list, outcomes: dict, odds: dict | None = None, limit: int = 4
) -> list[dict] | None:
    rows = _collect_buteur_rows(match_bets, outcomes, odds, limit)
    return rows if rows else None


def extract_score_exact_par_cote(
    match_bets: list, outcomes: dict, odds: dict | None = None, limit: int = 4
) -> list[dict] | None:
    try:
        for bet in match_bets:
            name = _bet_label(bet)
            if not _is_score_exact_bet(name):
                continue
            rows: list[dict] = []
            for oid in bet.get("outcomes") or []:
                out = lookup(outcomes, oid)
                if not out:
                    continue
                label = str(out.get("label") or "").strip()
                if not label:
                    continue
                price = lookup_odd(odds or {}, oid)
                if price is None:
                    continue
                item: dict = {
                    "score": label,
                    "cote": round(float(price), 2),
                }
                pct = _outcome_pct(out)
                if pct is not None:
                    item["prob"] = pct
                rows.append(item)
            if rows:
                rows.sort(key=lambda x: x.get("cote", 999))
                return rows[:limit]
    except Exception:
        pass
    return None


def build_marches_supplementaires(
    match_bets: list, outcomes: dict, odds: dict | None = None
) -> dict:
    pm = extract_plus_moins_buts(match_bets, outcomes, odds)
    buteurs = _collect_buteur_rows(match_bets, outcomes, odds, 5)
    return {
        "plus_moins_buts": pm,
        "buteur_match": buteurs if buteurs else None,
        "buteur_mi_temps": None,
        "buteur_multiple": None,
    }


def apply_annex_markets(
    record: dict, match_bets: list, outcomes: dict, odds: dict | None = None
) -> dict:
    """Marchés annexes (cotes) + structure legacy pour les value bets."""
    if is_match_live({"status": record.get("match_status")}):
        return record
    try:
        btts = extract_btts_cotes(match_bets, outcomes, odds)
        ou25 = extract_over_under_25_cotes(match_bets, outcomes, odds)
        buteurs = extract_buteurs_annexe(match_bets, outcomes, odds)
        score_exact = extract_score_exact_par_cote(match_bets, outcomes, odds)
        record["marches_supplementaires"] = build_marches_supplementaires(
            match_bets, outcomes, odds
        )
        if btts:
            record["btts"] = btts
        if record.get("les_deux_marquent") is None:
            pct = extract_btts_oui(match_bets, outcomes)
            if pct is not None:
                record["les_deux_marquent"] = pct
        if ou25:
            record["over_under_25"] = ou25
        if buteurs:
            record["buteurs"] = buteurs
        if score_exact:
            record["score_exact"] = score_exact
        if ou25 and ou25.get("plus") is not None:
            pm = record["marches_supplementaires"]["plus_moins_buts"]
            line = pm.get("2.5") if isinstance(pm, dict) else None
            if isinstance(line, dict) and line.get("plus_prob") is not None:
                record["tendance_buts"] = tendance_buts(
                    line.get("plus_prob"),
                    record.get("probabilites") or {},
                )
    except Exception:
        pass
    return record


def extract_btts_oui(match_bets: list, outcomes: dict) -> int | None:
    try:
        for bet in match_bets:
            name = str(bet.get("betTypeName") or bet.get("betTitle") or "").lower()
            if not (
                ("deux" in name and "marquent" in name)
                or "btts" in name
                or "les 2" in name
            ):
                continue
            for oid in bet.get("outcomes") or []:
                out = lookup(outcomes, oid)
                if not out:
                    continue
                label = str(out.get("label") or "").lower()
                code = str(out.get("code") or "").lower()
                if "oui" in label or code in ("yes", "oui", "o"):
                    prob = out.get("percentDistribution") or out.get("probability")
                    if prob is None:
                        continue
                    p = float(prob)
                    if 0 < p <= 1:
                        return int(round(p * 100))
                    return int(round(p))
    except Exception:
        pass
    return None


MATCH_ENDED_STATUSES = frozenset({"ENDED", "CLOSED", "FINISHED", "CANCELLED", "CANCELED"})
WINAMAX_MATCH_STATUS_ENDED_NUM = 3
MATCH_FINISHED_GRACE_MINUTES = 120
MATCH_PARSER_HORIZON_HOURS = int(os.environ.get("VELORA_PARSER_HORIZON_HOURS", "48"))


def is_winamax_match_finished(
    raw_match: dict,
    match_start_ts: int | None = None,
    now: datetime | None = None,
) -> bool:
    """True si le match est terminé (statut Winamax ou coup d'envoi + 2 h)."""
    status = str(raw_match.get("status") or "").strip().upper()
    if status in MATCH_ENDED_STATUSES:
        return True
    try:
        if int(raw_match.get("matchStatus")) == WINAMAX_MATCH_STATUS_ENDED_NUM:
            return True
    except (TypeError, ValueError):
        pass
    if status in MATCH_LIVE_STATUSES:
        return False

    kickoff: datetime | None = None
    if match_start_ts is not None:
        try:
            ts = float(match_start_ts)
            if ts > 1e12:
                ts /= 1000.0
            kickoff = datetime.fromtimestamp(ts, tz=TZ_PARIS)
        except (TypeError, ValueError, OSError, OverflowError):
            kickoff = None
    if kickoff is None:
        raw_start = raw_match.get("matchStart") or raw_match.get("matchStartDate")
        if raw_start is not None:
            ts_val, _ = format_match_start(raw_start)
            if ts_val is not None:
                try:
                    kickoff = datetime.fromtimestamp(float(ts_val), tz=TZ_PARIS)
                except (TypeError, ValueError, OSError, OverflowError):
                    kickoff = None
    if kickoff is None:
        return False

    now = now or datetime.now(tz=TZ_PARIS)
    return now > kickoff + timedelta(minutes=MATCH_FINISHED_GRACE_MINUTES)


def is_match_within_parser_horizon(
    raw_match: dict,
    match_start_ts: int | None = None,
    now: datetime | None = None,
    hours: int | None = None,
) -> bool:
    """
    True si le coup d'envoi est dans les N prochaines heures (défaut 48 h).
    Exclut les matchs lointains (ex. août) et ceux déjà terminés.
    """
    horizon = hours if hours is not None else MATCH_PARSER_HORIZON_HOURS
    now = now or datetime.now(tz=TZ_PARIS)
    kickoff: datetime | None = None

    if match_start_ts is not None:
        try:
            ts = float(match_start_ts)
            if ts > 1e12:
                ts /= 1000.0
            kickoff = datetime.fromtimestamp(ts, tz=TZ_PARIS)
        except (TypeError, ValueError, OSError, OverflowError):
            kickoff = None

    if kickoff is None:
        raw_start = raw_match.get("matchStart") or raw_match.get("matchStartDate")
        if raw_start is not None:
            ts_val, _ = format_match_start(raw_start)
            if ts_val is not None:
                try:
                    kickoff = datetime.fromtimestamp(float(ts_val), tz=TZ_PARIS)
                except (TypeError, ValueError, OSError, OverflowError):
                    kickoff = None

    if kickoff is None:
        return False

    if is_winamax_match_finished(raw_match, match_start_ts, now):
        return False

    earliest = now - timedelta(minutes=MATCH_FINISHED_GRACE_MINUTES)
    latest = now + timedelta(hours=horizon)
    return earliest <= kickoff <= latest


def format_match_start(raw) -> tuple[int | None, str]:
    """Convertit matchStart (s ou ms) en timestamp triable + libellé FR."""
    if raw is None:
        return None, "Date à confirmer"
    try:
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        dt = datetime.fromtimestamp(ts, tz=TZ_PARIS)
        return int(ts), dt.strftime("%d/%m/%Y à %H:%M")
    except Exception:
        return None, "Date à confirmer"


def tendance_buts(over_prob: int | None, probs: dict[str, int]) -> str:
    try:
        if over_prob is not None:
            return "Match Offensif" if over_prob > 55 else "Match Tactique"
        pn = probs.get("N", 0)
        if pn < 26 and (probs.get("1", 0) + probs.get("2", 0)) > 70:
            return "Match Offensif"
    except Exception:
        pass
    return "Match Tactique"


def build_velora_record(
    match_id,
    home: str,
    away: str,
    cotes: dict,
    probs: dict,
    match_bets: list,
    outcomes: dict,
    date_match: str,
    match_start_ts: int | None = None,
    odds: dict | None = None,
    match_status: str | None = None,
) -> dict:
    cotes_out = {
        "1": cotes.get("1"),
        "N": cotes.get("N"),
        "2": cotes.get("2"),
    }
    over_prob = extract_over_25_prob(match_bets, outcomes)
    btts = extract_btts_oui(match_bets, outcomes)
    tendance = tendance_buts(over_prob, probs)
    record = {
        "id_match": str(match_id),
        "date_match": date_match,
        "match_start_ts": match_start_ts,
        "equipe_domicile": home,
        "equipe_exterieur": away,
        "cotes": cotes_out,
        "probabilites": {
            "1": probs.get("1", 0),
            "N": probs.get("N", 0),
            "2": probs.get("2", 0),
        },
        "tendance_buts": tendance,
        "top_scores": extract_top_scores(match_bets, outcomes, odds),
    }
    if match_status:
        record["match_status"] = str(match_status).strip().upper()
    if btts is not None:
        record["les_deux_marquent"] = btts
    apply_annex_markets(record, match_bets, outcomes, odds)
    if record.get("les_deux_marquent") is None and btts is not None:
        record["les_deux_marquent"] = btts
    return apply_velora_analysis(record)


def parse_football_matches(data: dict) -> list[dict]:
    matches_map = data.get("matches") or {}
    bets_all = data.get("bets") or {}
    outcomes = data.get("outcomes") or {}
    odds = data.get("odds") or {}
    results = []
    skipped_finished = 0
    skipped_horizon = 0
    skipped_live = 0
    now = datetime.now(tz=TZ_PARIS)

    for match_id, match in matches_map.items():
        try:
            if not isinstance(match, dict) or match.get("sportId") != FOOTBALL_SPORT_ID:
                continue
            if is_match_live(match):
                skipped_live += 1
                continue
            home, away = get_teams(match)
            if home == "?" and away == "?":
                continue
            mid = match.get("matchId") or match_id
            sort_ts, date_match = format_match_start(match.get("matchStart"))
            if is_winamax_match_finished(match, sort_ts, now):
                skipped_finished += 1
                continue
            if not is_match_within_parser_horizon(match, sort_ts, now):
                skipped_horizon += 1
                continue
            mbets = bets_for_match(bets_all, mid)
            bet = find_main_bet(mbets, match.get("mainBetId"))
            if not bet:
                continue
            cotes, raw_pct = extract_1n2_from_bet(bet, outcomes, odds)
            probs = finalize_probabilities(raw_pct, cotes)
            api = build_velora_record(
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
                match_status=match.get("status"),
            )
            results.append({
                "api": api,
                "home": home,
                "away": away,
                "sort_ts": sort_ts if sort_ts is not None else 2**62,
            })
        except Exception:
            continue

    results.sort(key=lambda m: m["sort_ts"])
    if skipped_finished:
        print(
            f"[parser] {skipped_finished} match(s) terminé(s) exclus "
            f"(statut ENDED ou > {MATCH_FINISHED_GRACE_MINUTES} min après coup d'envoi)."
        )
    if skipped_horizon:
        print(
            f"[parser] {skipped_horizon} match(s) hors fenêtre "
            f"(>{MATCH_PARSER_HORIZON_HOURS} h ou date inconnue) — ignorés du JSON."
        )
    if skipped_live:
        print(
            f"[parser] {skipped_live} match(s) LIVE exclus "
            f"(cotes annexes / O-U non fiables)."
        )
    return results


def load_preloaded_state() -> dict | None:
    try:
        raw = json.loads(DUMP.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[parser] ECHEC: {DUMP} introuvable")
        return None
    except json.JSONDecodeError as e:
        print(f"[parser] ECHEC: JSON invalide ({e})")
        return None
    if isinstance(raw, dict) and raw.get("matches"):
        return raw
    return None


def stars_display(n: int) -> str:
    try:
        return "⭐" * max(1, min(5, int(n)))
    except Exception:
        return "⭐"


def main() -> None:
    print(f"[parser] Lecture {DUMP.name}...")
    data = load_preloaded_state()
    if not data:
        return

    use_v2 = os.environ.get("VELORA_API_V1", "").strip().lower() not in (
        "1",
        "true",
        "yes",
    )
    if use_v2:
        try:
            from velora_engine.analysis.pipeline import build_api_document_from_state
            from velora_engine.models import document_to_json

            doc = build_api_document_from_state(data)
            n = len(doc.matchs)
            print(f"[parser] {n} match(s) football — schema v{doc.schema_version}.")
            if not n:
                OUT_API.write_text(
                    json.dumps(doc.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print("[parser] ECHEC: aucun match exporte.")
                return
            OUT_API.write_text(document_to_json(doc), encoding="utf-8")
            print(f"[parser] Export v2 -> {OUT_API} ({OUT_API.stat().st_size // 1024} Ko)\n")
            for m in doc.matchs[:DISPLAY_LIMIT]:
                fa = m.free_analysis
                c = fa.cotes_1n2
                p = fa.probabilites
                conseil = fa.primary_pick.conseil_short if fa.primary_pick else "—"
                line = (
                    f"[Match] {m.date_match} | {m.equipe_domicile} - {m.equipe_exterieur} | "
                    f"Cotes : 1:{c.get('1')} N:{c.get('N')} 2:{c.get('2')} | "
                    f"Probas : {p['1']}%/{p['N']}%/{p['2']}% | "
                    f"{m.confidence.indice_velora}* | {conseil}"
                )
                print(line)
            if n > DISPLAY_LIMIT:
                print(f"... +{n - DISPLAY_LIMIT} matchs")
            return
        except Exception as e:
            print(f"[parser] Export v2 echoue ({e}), repli schema v1…")

    parsed = parse_football_matches(data)
    api_list = [m["api"] for m in parsed]
    print(f"[parser] {len(api_list)} match(s) football enrichis (v1).")

    if not api_list:
        OUT_API.write_text("[]", encoding="utf-8")
        print("[parser] ECHEC: aucun match exporte.")
        return

    OUT_API.write_text(json.dumps(api_list, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[parser] Export v1 -> {OUT_API} ({OUT_API.stat().st_size // 1024} Ko)\n")

    for m in parsed[:DISPLAY_LIMIT]:
        a = m["api"]
        c = a["cotes"]
        p = a["probabilites"]
        line = (
            f"[Match] {a.get('date_match', '?')} | {a['equipe_domicile']} - {a['equipe_exterieur']} | "
            f"Cotes : 1:{c['1']} N:{c['N']} 2:{c['2']} | "
            f"Probas : {p['1']}%/{p['N']}%/{p['2']}% | "
            f"{stars_display(a['indice_velora'])} | {a['conseil']}"
        )
        print(line)
    if len(parsed) > DISPLAY_LIMIT:
        print(f"... +{len(parsed) - DISPLAY_LIMIT} matchs")


if __name__ == "__main__":
    main()
