"""
Extraction marchés Winamax — Velora Engine v2 (B2).

- Over/Under dynamiques (match entier)
- Buts par équipe (team goals)
- BTTS, score exact (top probas), buteurs (match / MT / doublé)
- Métadonnées compétition depuis PRELOADED_STATE
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from velora_engine.models import (
    CompetitionMeta,
    MarketOutcome,
    MarketsRaw,
    OuLine,
    ScoreExactRow,
    ScorerRow,
    TeamGoalsSide,
)
from velora_engine.scrape.winamax_state import (
    bet_label,
    bets_for_match,
    find_raw_match,
    lookup,
    lookup_odd,
    outcome_pct,
    parse_ou_side_line,
    resolve_category_name,
    resolve_tournament_name,
    sanitize_ou_cote,
)

# --- Filtres paris (alignés parser_winamax) ---

SCORER_BET_FILTER_NAMES = frozenset({"buteur", "buteurs", "marqueur", "marqueurs"})
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
    "combiné",
    "combo",
    "meilleur marqueur et",
    "issue du match",
    "qualification",
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
        "match nul",
        "nul",
    }
)
OUTCOME_1N2_CODES = frozenset({"1", "2", "x", "n", "draw", "home", "away"})
EARLY_CUP_ROUND_HINTS = (
    "1/64",
    "1/32",
    "1/16",
    "1/8",
    "16e",
    "32e",
    "64e",
    "tour 1",
    "tour 2",
    "preliminary",
    "preliminaire",
)


def _is_team_goals_bet(name: str, home: str = "", away: str = "") -> bool:
    if "mi-temps" in name or "mi temps" in name:
        return False
    h = home.lower().strip()
    a = away.lower().strip()
    if h and len(h) > 2 and h in name:
        return True
    if a and len(a) > 2 and a in name:
        return True
    if "nombre de buts" in name and any(
        x in name for x in ("équipe", "equipe", "domicile", "extérieur", "exterieur")
    ):
        return True
    if "marque" in name and any(
        x in name
        for x in (
            "équipe",
            "equipe",
            "domicile",
            "extérieur",
            "exterieur",
            "au moins",
            "plus de",
        )
    ):
        return True
    if re.search(r"équipe\s*[12]|equipe\s*[12]", name):
        return True
    return False


def _is_goals_total_match_bet(name: str, home: str = "", away: str = "") -> bool:
    """OU buts sur le match entier (pas par équipe)."""
    if "mi-temps" in name or "mi temps" in name or "1ère" in name or "1ere" in name:
        return False
    if _is_team_goals_bet(name, home, away):
        return False
    if "nombre de buts" in name:
        return True
    if "total" in name and "but" in name:
        return True
    if "plus/moins" in name and "but" in name:
        return True
    if "nb de buts" in name or "nb buts" in name:
        return True
    return False


def _team_side_from_bet_name(
    name: str, home: str, away: str
) -> str | None:
    if any(x in name for x in ("extérieur", "exterieur", "équipe 2", "equipe 2", "away")):
        return "away"
    if any(x in name for x in ("domicile", "équipe 1", "equipe 1", "home")):
        return "home"
    h = home.lower().strip()
    a = away.lower().strip()
    if h and len(h) > 2 and h in name:
        return "home"
    if a and len(a) > 2 and a in name:
        return "away"
    return None


def _is_btts_bet(name: str) -> bool:
    return (
        ("deux" in name and "marquent" in name)
        or "btts" in name
        or ("les 2" in name and "marquent" in name)
        or name in ("les 2 équipes marquent", "les 2 equipes marquent")
    )


def _is_score_exact_bet(name: str) -> bool:
    if "multichance" in name or "mi-temps" in name or "mi temps" in name:
        return False
    return "score correct" in name or "score exact" in name


def _is_scorer_market_bet(bet: dict) -> bool:
    if not isinstance(bet, dict):
        return False
    filter_name = str(bet.get("betFilterName") or "").strip().lower()
    type_name = str(bet.get("betTypeName") or bet.get("betTitle") or "").strip().lower()
    template = str(bet.get("template") or "").strip().lower()
    blob = f"{filter_name} {type_name} {template}"
    if any(x in blob for x in SCORER_BET_FORBIDDEN):
        return False
    if any(
        x in blob for x in ("doubl", "duo", "multiple", "2 joueurs", "2 joueur", "triple")
    ):
        return False
    if filter_name in SCORER_BET_FILTER_NAMES:
        return True
    if any(t in template for t in ("goalscorer", "scorer", "anytimegoalscorer")):
        return True
    if ("buteur du match" in type_name or "marqueur du match" in type_name) and (
        "buteur" in type_name or "marqueur" in type_name
    ):
        return True
    return False


def _is_buteur_mi_temps_market(name: str) -> bool:
    if "buteur" not in name and "buteurs" not in name and "marqueur" not in name:
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


def _outcome_is_1n2_or_generic(out: dict) -> bool:
    code = str(out.get("code") or "").strip().lower()
    if code in OUTCOME_1N2_CODES:
        return True
    label = str(out.get("label") or out.get("name") or "").strip().lower()
    if not label or label in _SKIP_SCORER_LABELS:
        return True
    if label in ("1", "2", "n", "x"):
        return True
    if "match nul" in label or label.startswith("plus de") or label.startswith("moins de"):
        return True
    if re.match(r"^\d+(\.\d+)?$", label):
        return True
    return False


def _extract_scorer_player_name(out: dict) -> str | None:
    if not isinstance(out, dict):
        return None
    player = out.get("player")
    if isinstance(player, dict):
        name = player.get("name") or player.get("fullName") or player.get("displayName")
        if name and not _outcome_is_1n2_or_generic({"label": str(name), "code": ""}):
            return str(name).strip()
    for key in ("playerName", "participantName", "scorerName", "competitorName"):
        val = out.get(key)
        if isinstance(val, str) and len(val.strip()) > 2:
            if not _outcome_is_1n2_or_generic({"label": val, "code": out.get("code")}):
                return val.strip()
    if _outcome_is_1n2_or_generic(out):
        return None
    label = str(out.get("label") or out.get("name") or "").strip()
    if len(label) < 3 or label.lower() in _SKIP_SCORER_LABELS:
        return None
    return label


def _btts_outcome_side(out: dict) -> str | None:
    label = str(out.get("label") or "").lower()
    code = str(out.get("code") or "").lower()
    if "oui" in label or code in ("yes", "oui", "o"):
        return "oui"
    if "non" in label or code in ("no", "non", "n"):
        return "non"
    return None


def _merge_ou_line(
    bucket: dict[str, dict],
    line: str,
    side: str,
    out: dict,
    odds: dict | None,
    outcome_id: Any = None,
) -> None:
    entry = bucket.setdefault(line, {})
    price = None
    if outcome_id is not None:
        price = sanitize_ou_cote(lookup_odd(odds, outcome_id))
    if price is None:
        price = sanitize_ou_cote(lookup_odd(odds, out.get("id") or out.get("outcomeId")))
    pct = outcome_pct(out)
    if price is not None:
        entry[f"{side}_cote"] = price
    if pct is not None:
        entry[f"{side}_prob"] = pct


def extract_over_under_total(
    match_bets: list,
    outcomes: dict,
    odds: dict | None = None,
    *,
    home: str = "",
    away: str = "",
) -> dict[str, OuLine]:
    buckets: dict[str, dict] = {}
    for bet in match_bets:
        name = bet_label(bet)
        if not _is_goals_total_match_bet(name, home, away):
            continue
        for oid in bet.get("outcomes") or []:
            out = lookup(outcomes, oid)
            if not out:
                continue
            side, line = parse_ou_side_line(out.get("label") or "")
            if not side or not line:
                continue
            _merge_ou_line(buckets, line, side, out, odds, oid)
    result: dict[str, OuLine] = {}
    for line, data in sorted(buckets.items(), key=lambda x: float(x[0])):
        if data.get("plus_cote") is not None or data.get("moins_cote") is not None:
            result[line] = OuLine(
                plus_cote=data.get("plus_cote"),
                moins_cote=data.get("moins_cote"),
                plus_prob=data.get("plus_prob"),
                moins_prob=data.get("moins_prob"),
            )
    return result


def extract_team_goals(
    match_bets: list,
    outcomes: dict,
    odds: dict | None,
    home: str,
    away: str,
) -> dict[str, TeamGoalsSide]:
    buckets: dict[str, dict[str, dict]] = {"home": {}, "away": {}}
    for bet in match_bets:
        name = bet_label(bet)
        if not _is_team_goals_bet(name, home, away):
            continue
        side = _team_side_from_bet_name(name, home, away)
        if not side:
            continue
        for oid in bet.get("outcomes") or []:
            out = lookup(outcomes, oid)
            if not out:
                continue
            ou_side, line = parse_ou_side_line(out.get("label") or "")
            if not ou_side or not line:
                continue
            _merge_ou_line(buckets[side], line, ou_side, out, odds, oid)
    out: dict[str, TeamGoalsSide] = {}
    def _lines_from_bucket(side_bucket: dict[str, dict]) -> dict[str, OuLine]:
        lines_out: dict[str, OuLine] = {}
        for ln, data in side_bucket.items():
            if not (data.get("plus_cote") or data.get("moins_cote")):
                continue
            lines_out[ln] = OuLine(
                plus_cote=data.get("plus_cote"),
                moins_cote=data.get("moins_cote"),
                plus_prob=data.get("plus_prob"),
                moins_prob=data.get("moins_prob"),
            )
        return lines_out

    if buckets["home"]:
        lines = _lines_from_bucket(buckets["home"])
        if lines:
            out["home"] = TeamGoalsSide(team_name=home, lines=lines)
    if buckets["away"]:
        lines = _lines_from_bucket(buckets["away"])
        if lines:
            out["away"] = TeamGoalsSide(team_name=away, lines=lines)
    return out


def _bet_filter_exact(bet: dict, name: str) -> bool:
    fn = str(bet.get("betFilterName") or "").strip().lower()
    return fn == name.lower()


def _bet_filter_contains(bet: dict, needle: str, *, exclude: tuple[str, ...] = ()) -> bool:
    blob = bet_label(bet)
    if any(ex in blob for ex in exclude):
        return False
    return needle in blob


def _parse_dc_pick(label: str) -> str | None:
    s = str(label or "").upper().replace(" ", "").replace("OU", "")
    if "1X" in s or ("DOM" in s.upper() and "NUL" in s.upper()):
        return "1x"
    if "X2" in s or ("EXT" in s.upper() and "NUL" in s.upper()):
        return "x2"
    if s in ("12", "1OR2", "DOMEXT") or ("DOM" in s.upper() and "EXT" in s.upper()):
        return "12"
    return None


def _parse_1n2_pick(label: str, code: str = "") -> str | None:
    c = str(code or "").strip().upper()
    if c in ("1", "HOME"):
        return "1"
    if c in ("2", "AWAY"):
        return "2"
    if c in ("N", "X", "DRAW"):
        return "N"
    lab = str(label or "").strip().lower()
    if lab in ("1", "n", "2", "x"):
        return "N" if lab == "x" else lab.upper()
    if "nul" in lab or "égalité" in lab or "egalite" in lab:
        return "N"
    if "dom" in lab or lab == "home":
        return "1"
    if "ext" in lab or lab == "away":
        return "2"
    return None


def _parse_dnb_pick(label: str, home: str, away: str) -> str | None:
    lab = str(label or "").strip().lower()
    h = home.lower().strip()
    a = away.lower().strip()
    if h and len(h) > 2 and h in lab:
        return "home"
    if a and len(a) > 2 and a in lab:
        return "away"
    if "équipe 1" in lab or "equipe 1" in lab or "domicile" in lab:
        return "home"
    if "équipe 2" in lab or "equipe 2" in lab or "extérieur" in lab or "exterieur" in lab:
        return "away"
    return None


def _collect_outcomes(
    bet: dict,
    outcomes: dict,
    odds: dict | None,
    pick_parser,
) -> dict[str, MarketOutcome]:
    rows: dict[str, MarketOutcome] = {}
    for oid in bet.get("outcomes") or []:
        out = lookup(outcomes, oid)
        if not out:
            continue
        pick = pick_parser(out)
        if not pick or pick in rows:
            continue
        price = lookup_odd(odds, oid)
        pct = outcome_pct(out)
        if price is None and pct is None:
            continue
        rows[pick] = MarketOutcome(
            cote=round(float(price), 2) if price is not None else None,
            prob=pct,
        )
    return rows


def extract_double_chance(
    match_bets: list,
    outcomes: dict,
    odds: dict | None = None,
) -> dict[str, MarketOutcome]:
    for bet in match_bets:
        if not (
            _bet_filter_exact(bet, "Double chance")
            or _bet_filter_contains(bet, "double chance", exclude=("mt ", "mi-temps", "nb ", "marquent"))
        ):
            continue
        rows = _collect_outcomes(
            bet,
            outcomes,
            odds,
            lambda o: _parse_dc_pick(str(o.get("label") or "")),
        )
        if rows:
            return rows
    return {}


def extract_dnb(
    match_bets: list,
    outcomes: dict,
    odds: dict | None,
    home: str,
    away: str,
) -> dict[str, MarketOutcome]:
    for bet in match_bets:
        if not (
            _bet_filter_exact(bet, "Remboursé si nul")
            or _bet_filter_contains(bet, "remboursé si nul", exclude=("mt ", "mi-temps"))
            or _bet_filter_contains(bet, "rembourse si nul", exclude=("mt ", "mi-temps"))
        ):
            continue
        rows = _collect_outcomes(
            bet,
            outcomes,
            odds,
            lambda o: _parse_dnb_pick(str(o.get("label") or ""), home, away),
        )
        if rows:
            return rows
    return {}


def extract_half_time_1n2(
    match_bets: list,
    outcomes: dict,
    odds: dict | None = None,
) -> dict[str, MarketOutcome]:
    for bet in match_bets:
        if not (
            _bet_filter_exact(bet, "MT résultat")
            or _bet_filter_contains(bet, "mt résultat", exclude=("double", "remboursé", "mi-temps/fin"))
            or _bet_filter_contains(bet, "mt resultat", exclude=("double", "rembourse"))
        ):
            continue
        rows = _collect_outcomes(
            bet,
            outcomes,
            odds,
            lambda o: _parse_1n2_pick(str(o.get("label") or ""), str(o.get("code") or "")),
        )
        if rows:
            return rows
    return {}


def extract_handicap(
    match_bets: list,
    outcomes: dict,
    odds: dict | None = None,
    *,
    home: str = "",
    away: str = "",
) -> dict[str, dict[str, MarketOutcome]]:
    buckets: dict[str, dict[str, MarketOutcome]] = {}
    for bet in match_bets:
        if not (
            _bet_filter_exact(bet, "Écart buts")
            or _bet_filter_contains(bet, "écart buts", exclude=("mt ", "mi-temps"))
            or _bet_filter_contains(bet, "format handicap", exclude=("mt ", "mi-temps"))
        ):
            continue
        line = str(bet.get("specialBetValue") or "").strip() or "0"
        side_bucket = buckets.setdefault(line, {})
        for oid in bet.get("outcomes") or []:
            out = lookup(outcomes, oid)
            if not out:
                continue
            label = str(out.get("label") or "").strip().lower()
            pick = _parse_1n2_pick(label, str(out.get("code") or ""))
            if not pick:
                if home.lower() in label:
                    pick = "1"
                elif away.lower() in label:
                    pick = "2"
            if not pick or pick in side_bucket:
                continue
            price = lookup_odd(odds, oid)
            pct = outcome_pct(out)
            if price is None and pct is None:
                continue
            side_bucket[pick] = MarketOutcome(
                cote=round(float(price), 2) if price is not None else None,
                prob=pct,
            )
    return {ln: sides for ln, sides in buckets.items() if sides}


def extract_exact_goals(
    match_bets: list,
    outcomes: dict,
    odds: dict | None = None,
) -> dict[str, MarketOutcome]:
    for bet in match_bets:
        blob = bet_label(bet)
        if "nb exact" not in blob and "nombre exact" not in blob:
            continue
        if any(x in blob for x in ("équipe", "equipe", "mi-temps", "mi temps", "mt ")):
            continue
        rows: dict[str, MarketOutcome] = {}
        for oid in bet.get("outcomes") or []:
            out = lookup(outcomes, oid)
            if not out:
                continue
            label = str(out.get("label") or "").strip()
            m = re.match(r"^(\d+)", label)
            if not m:
                continue
            pick = m.group(1)
            if pick in rows:
                continue
            price = lookup_odd(odds, oid)
            pct = outcome_pct(out)
            if price is None and pct is None:
                continue
            rows[pick] = MarketOutcome(
                cote=round(float(price), 2) if price is not None else None,
                prob=pct,
            )
        if rows:
            return rows
    return {}


def extract_btts(
    match_bets: list, outcomes: dict, odds: dict | None = None
) -> dict[str, float | None] | None:
    for bet in match_bets:
        name = bet_label(bet)
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
            price = lookup_odd(odds, oid)
            if price is not None:
                row[side] = round(float(price), 2)
        if row["oui"] is not None or row["non"] is not None:
            return row
    return None


def _collect_scorer_rows(
    match_bets: list,
    outcomes: dict,
    odds: dict | None,
    matcher,
    limit: int = 5,
) -> list[ScorerRow]:
    rows: list[ScorerRow] = []
    seen: set[str] = set()
    for bet in match_bets:
        name = bet_label(bet)
        use_strict = matcher is _is_scorer_match_market
        if use_strict and not _is_scorer_market_bet(bet):
            continue
        if not use_strict and not matcher(name):
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
            rows.append(ScorerRow(joueur=joueur, cote=round(float(price), 2)))
    rows.sort(key=lambda x: x.cote)
    return rows[:limit]


def _is_scorer_match_market(name: str) -> bool:
    return False  # filtre via _is_scorer_market_bet sur bet dict


def extract_score_exact_top(
    match_bets: list, outcomes: dict, odds: dict | None = None, limit: int = 3
) -> list[ScoreExactRow]:
    for bet in match_bets:
        name = bet_label(bet)
        if not _is_score_exact_bet(name):
            continue
        rows: list[ScoreExactRow] = []
        for oid in bet.get("outcomes") or []:
            out = lookup(outcomes, oid)
            if not out:
                continue
            label = str(out.get("label") or "").strip()
            if not label:
                continue
            pct = outcome_pct(out)
            price = lookup_odd(odds, oid)
            rows.append(
                ScoreExactRow(
                    score=label,
                    prob=pct,
                    cote=round(float(price), 2) if price is not None else None,
                )
            )
        if rows:
            rows.sort(
                key=lambda r: (r.prob is not None, r.prob or 0),
                reverse=True,
            )
            return rows[:limit]
    return []


def _infer_competition_type(name: str) -> str:
    low = name.lower()
    if any(x in low for x in ("friendly", "friendlies", "amical", "amicaux", "club friend")):
        return "friendly"
    if any(x in low for x in ("coupe", "cup", "trophy", "cdm", "copa", "pokal")):
        return "cup"
    if any(
        x in low for x in ("champions", "europa", "conference", "ldc", "ligue des champions")
    ):
        return "international"
    if any(x in low for x in ("liga", "ligue", "league", "serie", "premier", "bundesliga")):
        return "league"
    return "other"


def _infer_stakes_tier(comp_type: str, name: str, round_label: str | None) -> str:
    low = name.lower()
    if comp_type == "friendly":
        return "low"
    if comp_type == "international" and any(
        x in low for x in ("champions", "ldc", "europa")
    ):
        return "high"
    if comp_type == "cup":
        if round_label and any(h in round_label.lower() for h in EARLY_CUP_ROUND_HINTS):
            return "low"
        return "medium"
    if comp_type == "league":
        return "medium"
    return "medium"


def extract_competition_meta(
    raw_match: dict | None,
    state: dict | None = None,
    *,
    fallback_title: str | None = None,
) -> CompetitionMeta:
    """Dérive nom / type / enjeu depuis PRELOADED_STATE."""
    state = state or {}
    raw = raw_match or {}
    name_parts: list[str] = []
    cat_id = raw.get("categoryId") or raw.get("category")
    tour_id = raw.get("tournamentId") or raw.get("tournament")
    if state and cat_id is not None:
        cn = resolve_category_name(state, cat_id)
        if cn:
            name_parts.append(cn)
    if state and tour_id is not None:
        tn = resolve_tournament_name(state, tour_id)
        if tn and tn not in name_parts:
            name_parts.append(tn)
    for key in (
        "competitionName",
        "tournamentName",
        "categoryName",
        "leagueName",
        "sportName",
    ):
        val = raw.get(key)
        if val and str(val).strip() not in name_parts:
            name_parts.append(str(val).strip())
    name = " — ".join(name_parts) if name_parts else (fallback_title or "Compétition inconnue")
    round_label = None
    for key in ("roundName", "round", "phaseName", "groupName"):
        val = raw.get(key)
        if val:
            round_label = str(val).strip()
            break
    comp_type = _infer_competition_type(name)
    stakes = _infer_stakes_tier(comp_type, name, round_label)
    return CompetitionMeta(
        name=name,
        type=comp_type,
        round=round_label,
        stakes_tier=stakes,
        category_id=str(cat_id) if cat_id is not None else None,
        tournament_id=str(tour_id) if tour_id is not None else None,
    )


@dataclass
class ExtractedMarkets:
    """Résultat brut extraction — prêt pour le pipeline value (B3+)."""

    markets_raw: MarketsRaw
    competition: CompetitionMeta
    score_exact_top3: list[ScoreExactRow] = field(default_factory=list)
    buteur_match: list[ScorerRow] = field(default_factory=list)
    buteur_mi_temps: list[ScorerRow] = field(default_factory=list)
    buteur_double: list[ScorerRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "markets_raw": self.markets_raw.to_dict(),
            "competition": self.competition.to_dict(),
            "score_exact_top3": [r.to_dict() for r in self.score_exact_top3],
            "buteur_match": [r.to_dict() for r in self.buteur_match],
            "buteur_mi_temps": [r.to_dict() for r in self.buteur_mi_temps],
            "buteur_double": [r.to_dict() for r in self.buteur_double],
        }


def extract_all_markets(
    match_bets: list,
    outcomes: dict,
    odds: dict | None,
    *,
    home: str,
    away: str,
    raw_match: dict | None = None,
    state: dict | None = None,
) -> ExtractedMarkets:
    """Point d'entrée unique B2."""
    ou_total = extract_over_under_total(match_bets, outcomes, odds, home=home, away=away)
    team_goals = extract_team_goals(match_bets, outcomes, odds, home, away)
    btts = extract_btts(match_bets, outcomes, odds)
    markets_raw = MarketsRaw(
        over_under_total=ou_total,
        btts=btts,
        team_goals=team_goals,
        double_chance=extract_double_chance(match_bets, outcomes, odds),
        dnb=extract_dnb(match_bets, outcomes, odds, home, away),
        half_time_1n2=extract_half_time_1n2(match_bets, outcomes, odds),
        handicap=extract_handicap(match_bets, outcomes, odds, home=home, away=away),
        exact_goals=extract_exact_goals(match_bets, outcomes, odds),
    )
    competition = extract_competition_meta(
        raw_match,
        state,
        fallback_title=f"{home} — {away}",
    )
    return ExtractedMarkets(
        markets_raw=markets_raw,
        competition=competition,
        score_exact_top3=extract_score_exact_top(match_bets, outcomes, odds, 3),
        buteur_match=_collect_scorer_rows(
            match_bets, outcomes, odds, _is_scorer_match_market, 5
        ),
        buteur_mi_temps=_collect_scorer_rows(
            match_bets, outcomes, odds, _is_buteur_mi_temps_market, 3
        ),
        buteur_double=_collect_scorer_rows(
            match_bets, outcomes, odds, _is_buteur_double_market, 3
        ),
    )


def extract_from_state(
    state: dict,
    match_id: Any,
    home: str,
    away: str,
) -> ExtractedMarkets | None:
    """Helper : state PRELOADED complet + id match."""
    if not state:
        return None
    mbets = bets_for_match(state.get("bets") or {}, match_id)
    if not mbets:
        return None
    raw = find_raw_match(state, match_id)
    return extract_all_markets(
        mbets,
        state.get("outcomes") or {},
        state.get("odds"),
        home=home,
        away=away,
        raw_match=raw,
        state=state,
    )
