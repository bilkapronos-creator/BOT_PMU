"""
Parse dump Winamax — Tennis (sportId 5) : vainqueur 2-way, sets, jeux, conseils Velora.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from parser_winamax import (
    DUMP,
    TZ_PARIS,
    bets_for_match,
    format_match_start,
    get_teams,
    is_match_live,
    is_match_within_parser_horizon,
    is_winamax_match_finished,
    lookup,
    lookup_odd,
)

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

TENNIS_SPORT_ID = 5
OUT_API = Path(__file__).resolve().parent / "api_velora_matchs_tennis.json"
VALUE_EDGE_MIN = 1.07
UNDERDOG_COTE_MIN = 1.55
FAVORI_STRONG = 1.45


def load_preloaded_state() -> dict | None:
    if not DUMP.is_file():
        print(f"[parser_tennis] {DUMP.name} introuvable.")
        return None
    try:
        return json.loads(DUMP.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[parser_tennis] Lecture dump : {e}")
        return None


def find_tennis_main_bet(bets: list[dict], main_bet_id) -> dict | None:
    if main_bet_id:
        for bet in bets:
            if bet.get("betId") == main_bet_id or str(bet.get("betId")) == str(main_bet_id):
                return bet
    for bet in bets:
        try:
            tpl = str(bet.get("template") or "").lower()
            fn = str(bet.get("betFilterName") or "").strip().lower()
            tn = str(bet.get("betTypeName") or bet.get("betTitle") or "").strip().lower()
            fid = bet.get("betFilterId")
            if tpl == "listodd" and ("vainqueur" in fn or "vainqueur" in tn):
                return bet
            if fid in (112, 26) and tpl == "listodd":
                return bet
        except Exception:
            continue
    return None


def extract_2way_from_bet(bet: dict, outcomes: dict, odds: dict) -> tuple[dict, dict]:
    cotes: dict[str, float | None] = {"1": None, "2": None}
    raw_pct: dict[str, float | None] = {"1": None, "2": None}
    for oid in bet.get("outcomes") or []:
        out = lookup(outcomes, oid)
        if not out:
            continue
        code = str(out.get("code") or "").strip().lower()
        label = str(out.get("label") or out.get("name") or "").strip().lower()
        price = lookup_odd(odds, oid)
        pct = out.get("percentDistribution") or out.get("probability")
        if pct is not None:
            try:
                val = float(pct)
                if 0 < val <= 1:
                    val *= 100
                key = "1" if code in ("1", "home", "p1") else "2" if code in ("2", "away", "p2") else None
                if key:
                    raw_pct[key] = val
            except (TypeError, ValueError):
                pass
        if code in ("1", "home", "p1"):
            cotes["1"] = price
        elif code in ("2", "away", "p2"):
            cotes["2"] = price
        elif price and label and cotes["1"] is None:
            cotes["1"] = price
        elif price and label and cotes["2"] is None:
            cotes["2"] = price
    return cotes, raw_pct


def implied_2way(cotes: dict[str, float | None]) -> dict[str, int]:
    inv: dict[str, float] = {}
    for key in ("1", "2"):
        c = cotes.get(key)
        if c and c > 0:
            inv[key] = 1.0 / c
    total = sum(inv.values())
    if total <= 0:
        return {"1": 50, "2": 50}
    out = {k: int(round(100 * v / total)) for k, v in inv.items()}
    diff = 100 - out.get("1", 0) - out.get("2", 0)
    if diff:
        kmax = "1" if out.get("1", 0) >= out.get("2", 0) else "2"
        out[kmax] = out.get(kmax, 0) + diff
    return out


def finalize_2way_probabilities(raw_pct: dict, cotes: dict) -> dict[str, int]:
    probs = implied_2way(cotes)
    for key in ("1", "2"):
        v = raw_pct.get(key)
        if v is not None:
            try:
                p = int(round(float(v)))
                if 5 <= p <= 95:
                    probs[key] = p
            except (TypeError, ValueError):
                pass
    diff = 100 - probs.get("1", 0) - probs.get("2", 0)
    if diff:
        kmax = "1" if probs.get("1", 0) >= probs.get("2", 0) else "2"
        probs[kmax] = probs.get(kmax, 0) + diff
    return probs


def _bet_blob(bet: dict) -> str:
    parts = [
        bet.get("betFilterName"),
        bet.get("betTypeName"),
        bet.get("betTitle"),
        bet.get("specialBetValue"),
    ]
    return " ".join(str(p) for p in parts if p).strip().lower()


def _parse_ou_outcome(label: str) -> tuple[str | None, str | None]:
    lab = label.strip().lower().replace(",", ".")
    m_plus = re.search(r"plus de\s+(\d+(?:\.\d+)?)", lab)
    m_moins = re.search(r"moins de\s+(\d+(?:\.\d+)?)", lab)
    if m_plus:
        return "plus", m_plus.group(1)
    if m_moins:
        return "moins", m_moins.group(1)
    return None, None


def extract_ou_markets(
    match_bets: list, outcomes: dict, odds: dict, keywords: tuple[str, ...]
) -> dict[str, dict]:
    buckets: dict[str, dict] = {}
    for bet in match_bets:
        name = _bet_blob(bet)
        if not any(k in name for k in keywords):
            continue
        for oid in bet.get("outcomes") or []:
            out = lookup(outcomes, oid)
            if not out:
                continue
            label = str(out.get("label") or out.get("name") or "")
            side, line = _parse_ou_outcome(label)
            if not side or not line:
                continue
            price = lookup_odd(odds, oid)
            if price is None:
                continue
            entry = buckets.setdefault(line, {})
            entry[f"{side}_cote"] = round(float(price), 2)
    return buckets


def extract_score_exact_sets(
    match_bets: list, outcomes: dict, odds: dict, limit: int = 4
) -> list[dict]:
    rows: list[dict] = []
    for bet in match_bets:
        name = _bet_blob(bet)
        if "score exact" not in name and "score correct" not in name:
            continue
        if "jeu" in name and "set" not in name:
            continue
        for oid in bet.get("outcomes") or []:
            out = lookup(outcomes, oid)
            if not out:
                continue
            label = str(out.get("label") or "").strip()
            if not label or "-" not in label and ":" not in label:
                continue
            price = lookup_odd(odds, oid)
            if price is None:
                continue
            rows.append({"score": label.replace(":", "-"), "cote": round(float(price), 2)})
        if rows:
            break
    rows.sort(key=lambda x: x.get("cote", 999))
    return rows[:limit]


def competition_name(match: dict, state: dict) -> str:
    try:
        tid = match.get("tournamentId")
        tournaments = state.get("tournaments") or {}
        t = tournaments.get(str(tid)) or tournaments.get(tid)
        if isinstance(t, dict) and t.get("name"):
            return str(t["name"])
        cid = match.get("categoryId")
        categories = state.get("categories") or {}
        c = categories.get(str(cid)) or categories.get(cid)
        if isinstance(c, dict) and c.get("name"):
            return str(c["name"])
    except Exception:
        pass
    return "Tennis"


def _edge_score(prob_pct: int, cote: float | None) -> float:
    if not cote or cote <= 1:
        return 0.0
    return (prob_pct / 100.0) * float(cote)


def build_conseils_tennis(
    home: str,
    away: str,
    cotes: dict,
    probs: dict[str, int],
    marches: dict,
) -> tuple[list[dict], dict | None]:
    conseils: list[dict] = []
    c1, c2 = cotes.get("1"), cotes.get("2")
    p1, p2 = probs.get("1", 0), probs.get("2", 0)
    fav = "1" if (c1 or 99) <= (c2 or 99) else "2"
    dog = "2" if fav == "1" else "1"
    fav_name = home if fav == "1" else away
    dog_name = away if fav == "1" else home
    fav_cote = cotes.get(fav)
    dog_cote = cotes.get(dog)
    fav_prob = probs.get(fav, 0)
    dog_prob = probs.get(dog, 0)

    def _row(market: str, pick: str, label: str, cote, prob_pct: int, tier: str, raison: str):
        edge = _edge_score(prob_pct, cote)
        return {
            "market": market,
            "pick": pick,
            "label": label,
            "cote": round(float(cote), 2) if cote else None,
            "prob_pct": prob_pct,
            "edge": round(edge, 3),
            "score": round(edge - 1.0, 3),
            "tier": tier,
            "raison": raison,
            "stars": 5 if tier == "excellent" else 4 if tier == "bon" else 3,
        }

    if dog_cote and dog_cote >= UNDERDOG_COTE_MIN:
        edge_d = _edge_score(dog_prob, dog_cote)
        if edge_d >= VALUE_EDGE_MIN:
            conseils.append(
                _row(
                    "vainqueur",
                    dog,
                    f"Vainqueur {dog_name}",
                    dog_cote,
                    dog_prob,
                    "excellent" if edge_d >= 1.15 else "bon",
                    f"Outsider {dog_name} : proba {dog_prob}% × cote {dog_cote:.2f} → edge {edge_d:.2f}",
                )
            )

    if fav_cote and fav_cote <= FAVORI_STRONG and fav_prob >= 58:
        conseils.append(
            _row(
                "vainqueur",
                fav,
                f"Vainqueur {fav_name}",
                fav_cote,
                fav_prob,
                "bon",
                f"Favori net {fav_name} ({fav_prob}% implicite, cote {fav_cote:.2f})",
            )
        )

    sets = marches.get("sets_total") or {}
    for line, ou in sorted(sets.items(), key=lambda x: float(x[0])):
        plus = ou.get("plus_cote")
        moins = ou.get("moins_cote")
        if plus and float(line) >= 2.5:
            conseils.append(
                _row(
                    "sets_over",
                    f"over_{line}",
                    f"Plus de {line} sets",
                    plus,
                    min(72, max(35, p1 + 5)),
                    "bon",
                    f"Match potentiellement long — marché +{line} sets à {plus:.2f}",
                )
            )
        if moins and float(line) <= 2.5 and fav_cote and fav_cote < 1.55:
            conseils.append(
                _row(
                    "sets_under",
                    f"under_{line}",
                    f"Moins de {line} sets",
                    moins,
                    min(70, fav_prob),
                    "bon",
                    f"Favori dominant — moins de {line} sets à {moins:.2f}",
                )
            )

    games = marches.get("games_total") or {}
    g22 = games.get("22.5") or games.get("21.5")
    if isinstance(g22, dict) and g22.get("plus_cote"):
        conseils.append(
            _row(
                "games_over",
                "over_22.5",
                "Plus de 22.5 jeux",
                g22["plus_cote"],
                52,
                "bon",
                "Volume de jeux élevé attendu sur le marché Winamax",
            )
        )

    conseils.sort(key=lambda x: (-(x.get("edge") or 0), x.get("cote") or 0))
    meilleur = conseils[0] if conseils else None
    if not meilleur and fav_cote:
        pick_name = fav_name
        meilleur = _row(
            "vainqueur",
            fav,
            f"Vainqueur {pick_name}",
            fav_cote,
            fav_prob,
            "standard",
            f"Pronostic marché : {pick_name} ({fav_prob}%)",
        )
        conseils.append(meilleur)
    return conseils, meilleur


def build_tennis_record(
    match_id,
    home: str,
    away: str,
    cotes: dict,
    probs: dict,
    match_bets: list,
    outcomes: dict,
    odds: dict,
    date_match: str,
    match_start_ts: int | None,
    competition: str,
    match_status: str | None = None,
) -> dict:
    sets_ou = extract_ou_markets(
        match_bets, outcomes, odds, ("nb de sets", "nombre de sets", "sets", "nb sets")
    )
    games_ou = extract_ou_markets(
        match_bets, outcomes, odds, ("nb de jeux", "nombre de jeux", "jeux", "total jeux")
    )
    score_sets = extract_score_exact_sets(match_bets, outcomes, odds)
    marches = {
        "sets_total": sets_ou,
        "games_total": games_ou,
        "score_exact_sets": score_sets,
    }
    conseils, meilleur = build_conseils_tennis(home, away, cotes, probs, marches)
    pronostic = meilleur.get("pick") if meilleur else ("1" if probs.get("1", 0) >= probs.get("2", 0) else "2")
    pick_name = home if pronostic == "1" else away

    record = {
        "id_match": str(match_id),
        "sport": "tennis",
        "date_match": date_match,
        "match_start_ts": match_start_ts,
        "match_status": match_status or "PREMATCH",
        "equipe_domicile": home,
        "equipe_exterieur": away,
        "joueur_1": home,
        "joueur_2": away,
        "meta_match": {
            "competition": {
                "name": competition,
                "type": "tennis",
                "stakes_tier": "medium",
            }
        },
        "free_analysis": {
            "cotes_2way": {"1": cotes.get("1"), "2": cotes.get("2")},
            "probabilites": {"1": probs.get("1", 0), "2": probs.get("2", 0)},
            "pronostic_vainqueur": pronostic,
            "pronostic_label": meilleur.get("label") if meilleur else f"Vainqueur {pick_name}",
            "confiance_niveau": "haute" if (probs.get(pronostic, 0) >= 62) else "moyenne" if probs.get(pronostic, 0) >= 52 else "faible",
            "marches": marches,
            "conseils_intelligents": conseils,
            "meilleur_conseil": meilleur,
            "value_bets": [c for c in conseils if (c.get("edge") or 0) >= VALUE_EDGE_MIN],
        },
        "conseil": meilleur.get("label") if meilleur else f"Vainqueur {pick_name}",
        "cotes": {"1": cotes.get("1"), "2": cotes.get("2")},
        "probabilites": {"1": probs.get("1", 0), "2": probs.get("2", 0)},
    }
    return record


def parse_tennis_matches(data: dict) -> list[dict]:
    matches_map = data.get("matches") or {}
    bets_all = data.get("bets") or {}
    outcomes = data.get("outcomes") or {}
    odds = data.get("odds") or {}
    results: list[dict] = []
    skipped = 0
    now = datetime.now(tz=TZ_PARIS)

    for match_id, match in matches_map.items():
        try:
            if not isinstance(match, dict) or match.get("sportId") != TENNIS_SPORT_ID:
                continue
            home, away = get_teams(match)
            if home == "?" and away == "?":
                continue
            mid = match.get("matchId") or match_id
            sort_ts, date_match = format_match_start(match.get("matchStart"))
            if is_winamax_match_finished(match, sort_ts, now):
                skipped += 1
                continue
            if not is_match_within_parser_horizon(match, sort_ts, now):
                skipped += 1
                continue
            mbets = bets_for_match(bets_all, mid)
            bet = find_tennis_main_bet(mbets, match.get("mainBetId"))
            if not bet:
                skipped += 1
                continue
            cotes, raw_pct = extract_2way_from_bet(bet, outcomes, odds)
            if not cotes.get("1") and not cotes.get("2"):
                skipped += 1
                continue
            probs = finalize_2way_probabilities(raw_pct, cotes)
            comp = competition_name(match, data)
            status = str(match.get("status") or "PREMATCH").strip().upper()
            if is_match_live(match):
                status = "LIVE"
            rec = build_tennis_record(
                mid,
                home,
                away,
                cotes,
                probs,
                mbets,
                outcomes,
                odds,
                date_match,
                sort_ts,
                comp,
                match_status=status,
            )
            results.append(rec)
        except Exception:
            continue

    if skipped:
        print(f"[parser_tennis] {skipped} match(s) ignoré(s) (terminés / sans cotes / hors fenêtre).")
    return results


def main() -> None:
    data = load_preloaded_state()
    if not data:
        return
    matchs = parse_tennis_matches(data)
    doc = {
        "schema_version": 2,
        "meta": {
            "sport": "tennis",
            "generated_at": datetime.now(tz=TZ_PARIS).isoformat(),
            "engine": "velora-tennis-1",
            "match_count": len(matchs),
        },
        "matchs": matchs,
    }
    OUT_API.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[parser_tennis] {len(matchs)} match(s) tennis -> {OUT_API.name}")
    for m in matchs[:8]:
        fa = m.get("free_analysis") or {}
        c = fa.get("cotes_2way") or {}
        p = fa.get("probabilites") or {}
        print(
            f"  {m.get('date_match')} | {m['joueur_1']} vs {m['joueur_2']} | "
            f"1:{c.get('1')} 2:{c.get('2')} | {p.get('1')}%/{p.get('2')}% | "
            f"{fa.get('pronostic_label', '—')}"
        )
    if len(matchs) > 8:
        print(f"  ... +{len(matchs) - 8} matchs")


if __name__ == "__main__":
    main()
