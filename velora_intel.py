"""
Analyse statistique Velora — forme récente (5 matchs), classement, face-à-face.
L'Indice Velora est un score pondéré (historique), pas un calcul basé sur les cotes.
"""
from __future__ import annotations

import re
from typing import Any

FORM_WIN = 3
FORM_DRAW = 1
FORM_LOSS = 0
FORM_CHARS = frozenset("WDL")

# Coefficients multiplicateurs (pondération utilisateur)
MULT_FORM_EXCELLENT = 1.30  # +30 % forme excellente (≥70 % points sur 5 matchs)
MULT_FORM_SOLIDE = 1.15  # +15 % forme solide (≥55 %)
MULT_CLASSEMENT_FORT = 1.20  # +20 % écart classement net (≥4 places)
MULT_CLASSEMENT_LEGER = 1.10  # +10 % écart modéré (≥2 places)
MULT_H2H_DOMINANT = 1.15  # +15 % face-à-face orienté

MIN_MATCHS_FORME = 3  # minimum de matchs connus par équipe pour calculer l'indice
INDICE_NON_CALCULABLE = 0
LABEL_NON_CALCULABLE = "Non calculable"


def parse_form_string(form: Any, max_len: int = 5) -> dict[str, Any]:
    """Parse une série Winamax (ex. « WLDDW ») sur les N derniers matchs."""
    raw = str(form or "").upper().strip()
    letters = [c for c in raw if c in FORM_CHARS][:max_len]
    points = sum(FORM_WIN if c == "W" else FORM_DRAW if c == "D" else FORM_LOSS for c in letters)
    wins = sum(1 for c in letters if c == "W")
    draws = sum(1 for c in letters if c == "D")
    losses = sum(1 for c in letters if c == "L")
    played = len(letters)
    max_pts = played * FORM_WIN if played else 0
    return {
        "raw": "".join(letters),
        "played": played,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "points": points,
        "max_points": max_pts,
        "pct": round(100 * points / max_pts, 1) if max_pts else 0,
    }


def _safe_int(val: Any) -> int | None:
    try:
        if val is None or val == "":
            return None
        return int(val)
    except (TypeError, ValueError):
        return None


def _find_raw_match(state: dict, match_id: Any) -> dict | None:
    matches = state.get("matches") or {}
    if not isinstance(matches, dict):
        return None
    key = str(match_id)
    if key in matches and isinstance(matches[key], dict):
        return matches[key]
    try:
        kid = int(match_id)
        if kid in matches and isinstance(matches[kid], dict):
            return matches[kid]
    except (TypeError, ValueError):
        pass
    for m in matches.values():
        if not isinstance(m, dict):
            continue
        if str(m.get("matchId")) == key:
            return m
    return None


def extract_intel_from_state(state: dict | None, match_id: Any) -> dict[str, Any]:
    """Forme 5 derniers matchs + classement depuis PRELOADED_STATE."""
    empty: dict[str, Any] = {
        "has_form": False,
        "home_form": {},
        "away_form": {},
        "home_rank": None,
        "away_rank": None,
        "h2h": None,
        "form_edge": 0,
        "rank_edge": 0,
    }
    if not state or not isinstance(state, dict):
        return empty

    raw = _find_raw_match(state, match_id)
    if not raw:
        return empty

    home_form = parse_form_string(raw.get("competitor1LastMatches"))
    away_form = parse_form_string(raw.get("competitor2LastMatches"))
    home_rank = _safe_int(raw.get("competitor1Ranking"))
    away_rank = _safe_int(raw.get("competitor2Ranking"))

    h2h = _parse_h2h_from_match(raw)
    has_form = home_form["played"] >= 2 and away_form["played"] >= 2

    form_edge = 0
    if has_form:
        form_edge = home_form["points"] - away_form["points"]

    rank_edge = 0
    if home_rank is not None and away_rank is not None:
        rank_edge = away_rank - home_rank

    return {
        "has_form": has_form,
        "home_form": home_form,
        "away_form": away_form,
        "home_rank": home_rank,
        "away_rank": away_rank,
        "h2h": h2h,
        "form_edge": form_edge,
        "rank_edge": rank_edge,
    }


def _parse_h2h_from_match(raw: dict) -> dict[str, Any] | None:
    """Face-à-face si présent dans l'état Winamax (champs variables)."""
    for key in (
        "headToHead",
        "headToHeadStats",
        "h2h",
        "competitor1HeadToHead",
        "versus",
    ):
        blob = raw.get(key)
        if isinstance(blob, dict):
            return _normalize_h2h_dict(blob)
        if isinstance(blob, str) and blob.strip():
            return _parse_h2h_text(blob)
    text = str(raw.get("headToHeadLabel") or raw.get("versusLabel") or "").strip()
    if text:
        parsed = _parse_h2h_text(text)
        if parsed:
            return parsed
    return None


def _normalize_h2h_dict(blob: dict) -> dict[str, Any] | None:
    home = _safe_int(blob.get("home") or blob.get("team1") or blob.get("wins1"))
    away = _safe_int(blob.get("away") or blob.get("team2") or blob.get("wins2"))
    draws = _safe_int(blob.get("draws") or blob.get("nul"))
    if home is None and away is None:
        return None
    return {
        "home_wins": home or 0,
        "away_wins": away or 0,
        "draws": draws or 0,
        "label": f"{home or 0}V-{draws or 0}N-{away or 0}V",
    }


def _parse_h2h_text(text: str) -> dict[str, Any] | None:
    nums = [int(x) for x in re.findall(r"\d+", text)]
    if len(nums) >= 3:
        return {"home_wins": nums[0], "draws": nums[1], "away_wins": nums[2], "label": text[:40]}
    if len(nums) == 2:
        return {"home_wins": nums[0], "away_wins": nums[1], "draws": 0, "label": text[:40]}
    return None


def extract_intel_from_page(page, match_id: Any) -> dict[str, Any]:
    """Complète l'intel via DOM (face-à-face) si absent du JSON."""
    try:
        extra = page.evaluate(
            """(mid) => {
                const s = window.PRELOADED_STATE;
                if (!s || !s.matches) return null;
                const key = String(mid);
                let m = s.matches[key] || s.matches[mid];
                if (!m) {
                    for (const k of Object.keys(s.matches)) {
                        const x = s.matches[k];
                        if (x && String(x.matchId) === key) { m = x; break; }
                    }
                }
                if (!m) return null;
                const h2hEl = document.querySelector('[class*="head-to-head"], [class*="headtohead"], [data-test*="h2h"]');
                let h2hText = h2hEl ? h2hEl.innerText.slice(0, 120) : '';
                if (!h2hText) {
                    const blocks = Array.from(document.querySelectorAll('section, div, article'));
                    for (const b of blocks) {
                        const t = (b.innerText || '').trim();
                        if (t.length < 400 && /face\\s*à\\s*face/i.test(t)) {
                            h2hText = t.slice(0, 120);
                            break;
                        }
                    }
                }
                return {
                    competitor1LastMatches: m.competitor1LastMatches || '',
                    competitor2LastMatches: m.competitor2LastMatches || '',
                    competitor1Ranking: m.competitor1Ranking,
                    competitor2Ranking: m.competitor2Ranking,
                    h2hText,
                };
            }""",
            str(match_id),
        )
    except Exception:
        return extract_intel_from_state(None, match_id)

    if not extra:
        return extract_intel_from_state(None, match_id)

    pseudo_state = {
        "matches": {
            str(match_id): {
                "matchId": match_id,
                "competitor1LastMatches": extra.get("competitor1LastMatches"),
                "competitor2LastMatches": extra.get("competitor2LastMatches"),
                "competitor1Ranking": extra.get("competitor1Ranking"),
                "competitor2Ranking": extra.get("competitor2Ranking"),
                "headToHeadLabel": extra.get("h2hText"),
            }
        }
    }
    return extract_intel_from_state(pseudo_state, match_id)


def intel_stats_suffisantes(intel: dict[str, Any]) -> bool:
    """Données historiques exploitables (pas d'estimation depuis les cotes)."""
    if not intel.get("has_form"):
        return False
    hf = intel.get("home_form") or {}
    af = intel.get("away_form") or {}
    return hf.get("played", 0) >= MIN_MATCHS_FORME and af.get("played", 0) >= MIN_MATCHS_FORME


def _pick_1n2_from_signals(
    probs: dict,
    form_edge: int,
    rank_edge: int,
    h2h: dict | None,
) -> tuple[str, float]:
    """Pick 1N2 priorité historique (probas Winamax en poids faible)."""
    p1 = float(probs.get("1") or 0) * 0.12
    pn = float(probs.get("N") or 0) * 0.10
    p2 = float(probs.get("2") or 0) * 0.12

    h2h_home = 0
    h2h_away = 0
    if h2h:
        h2h_home = int(h2h.get("home_wins") or 0)
        h2h_away = int(h2h.get("away_wins") or 0)

    score_1 = p1 + form_edge * 3.0 + rank_edge * 2.5 + h2h_home * 3.5
    score_n = pn + max(0, 10 - abs(form_edge)) * 0.5
    score_2 = p2 - form_edge * 3.0 - rank_edge * 2.5 + h2h_away * 3.5

    scores = {"1": score_1, "N": score_n, "2": score_2}
    pick = max(scores, key=scores.get)
    margin = scores[pick] - sorted(scores.values())[-2]
    return pick, margin


def compute_weighted_velora_score(intel: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    Score d'analyse 0–100 avec coefficients multiplicateurs cumulés.
    Base neutre + bonus forme + multiplicateurs forme / classement / H2H.
    """
    hf = intel.get("home_form") or {}
    af = intel.get("away_form") or {}
    base = 38.0
    base += min(12.0, abs(intel.get("form_edge") or 0) * 1.8)

    mult = 1.0
    factors: list[str] = []

    max_form_pct = max(float(hf.get("pct") or 0), float(af.get("pct") or 0))
    if max_form_pct >= 70:
        mult *= MULT_FORM_EXCELLENT
        factors.append("forme_excellente_+30%")
    elif max_form_pct >= 55:
        mult *= MULT_FORM_SOLIDE
        factors.append("forme_solide_+15%")

    hr = intel.get("home_rank")
    ar = intel.get("away_rank")
    if hr is not None and ar is not None:
        diff = abs(int(hr) - int(ar))
        if diff >= 4:
            mult *= MULT_CLASSEMENT_FORT
            factors.append("classement_favorable_+20%")
        elif diff >= 2:
            mult *= MULT_CLASSEMENT_LEGER
            factors.append("classement_leger_+10%")

    h2h = intel.get("h2h")
    if h2h:
        hw = int(h2h.get("home_wins") or 0)
        aw = int(h2h.get("away_wins") or 0)
        if max(hw, aw) >= 2 and abs(hw - aw) >= 1:
            mult *= MULT_H2H_DOMINANT
            factors.append("face_a_face_+15%")

    score = min(100.0, round(base * mult, 1))
    return score, {
        "base": round(base, 1),
        "multiplicateur": round(mult, 3),
        "facteurs": factors,
        "forme_dom_pct": hf.get("pct"),
        "forme_ext_pct": af.get("pct"),
        "classement_dom": hr,
        "classement_ext": ar,
    }


def _stars_from_weighted_score(score: float) -> int:
    """Indice 1–5 depuis le score pondéré (pas les cotes)."""
    if score >= 82:
        return 5
    if score >= 68:
        return 4
    if score >= 54:
        return 3
    if score >= 40:
        return 2
    return 1


def _truncate(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def build_conseil_from_intel(
    match: dict,
    intel: dict[str, Any],
    pick: str,
    stars: int,
) -> str:
    home = match.get("equipe_domicile") or "Domicile"
    away = match.get("equipe_exterieur") or "Extérieur"
    hf = intel.get("home_form") or {}
    af = intel.get("away_form") or {}
    h2h = intel.get("h2h")

    form_line = ""
    if hf.get("played") and af.get("played"):
        form_line = (
            f"Forme 5j: {home} {hf.get('wins', 0)}V/{hf.get('draws', 0)}N/{hf.get('losses', 0)}D "
            f"· {away} {af.get('wins', 0)}V/{af.get('draws', 0)}N/{af.get('losses', 0)}D"
        )

    rank_line = ""
    hr, ar = intel.get("home_rank"), intel.get("away_rank")
    if hr is not None and ar is not None:
        rank_line = f"Classement {hr}e vs {ar}e"

    labels = {"1": f"Victoire {home}", "N": "Match nul", "2": f"Victoire {away}"}
    pick_label = labels.get(pick, pick)

    parts = [pick_label]
    if form_line and intel.get("form_edge", 0) != 0:
        fav = home if intel["form_edge"] > 0 else away
        parts.append(f"forme favorable {fav}")
    if rank_line:
        parts.append(rank_line)
    if h2h and (h2h.get("home_wins") or h2h.get("away_wins")):
        parts.append(f"face-à-face {h2h.get('label', '')}")

    conseil = " — ".join(parts)
    if stars >= 4 and pick in ("1", "2"):
        conseil = f"🔥 {conseil}"
    return _truncate(conseil, 120)


def apply_non_calculable_velora(record: dict, reason: str | None = None) -> dict:
    """Indice non calculable : pas de fallback sur les cotes."""
    out = dict(record)
    out["indice_velora"] = INDICE_NON_CALCULABLE
    out["indice_velora_label"] = LABEL_NON_CALCULABLE
    out["velora_score"] = None
    out.pop("velora_confidence", None)
    if reason and not out.get("conseil"):
        out["conseil"] = _truncate(reason, 56)
    return out


def compute_velora_from_intel(match: dict, intel: dict[str, Any]) -> dict[str, Any]:
    """
    Indice 1–5 et conseil à partir des stats réelles uniquement (score pondéré).
    """
    if not intel_stats_suffisantes(intel):
        return {
            "applied": False,
            "indice_velora": INDICE_NON_CALCULABLE,
            "indice_velora_label": LABEL_NON_CALCULABLE,
            "reason": "forme_insuffisante",
        }

    probs = match.get("probabilites") or {}
    pick, margin = _pick_1n2_from_signals(
        probs,
        int(intel.get("form_edge") or 0),
        int(intel.get("rank_edge") or 0),
        intel.get("h2h"),
    )

    velora_score, breakdown = compute_weighted_velora_score(intel)
    stars = _stars_from_weighted_score(velora_score)
    conseil = build_conseil_from_intel(match, intel, pick, stars)

    return {
        "applied": True,
        "indice_velora": stars,
        "indice_velora_label": None,
        "velora_score": velora_score,
        "velora_score_breakdown": breakdown,
        "conseil": conseil,
        "pick_1n2": pick,
        "confidence": round(velora_score, 1),
        "signal_margin": round(margin, 2),
    }


def apply_statistical_velora_analysis(match: dict, intel: dict[str, Any]) -> dict:
    """Fusionne l'analyse stats dans le record match (sans écraser les marchés détail)."""
    out = dict(match)
    out["velora_intel"] = {
        "home_form": intel.get("home_form"),
        "away_form": intel.get("away_form"),
        "home_rank": intel.get("home_rank"),
        "away_rank": intel.get("away_rank"),
        "h2h": intel.get("h2h"),
        "form_edge": intel.get("form_edge"),
        "rank_edge": intel.get("rank_edge"),
    }
    computed = compute_velora_from_intel(out, intel)
    if computed.get("applied"):
        out["indice_velora"] = computed["indice_velora"]
        out["indice_velora_label"] = None
        out["velora_score"] = computed["velora_score"]
        out["velora_score_breakdown"] = computed.get("velora_score_breakdown")
        out["conseil"] = computed["conseil"]
        out["velora_pick_1n2"] = computed.get("pick_1n2")
        out["velora_confidence"] = computed.get("confidence")
        if computed["indice_velora"] >= 4:
            out["is_opportunite"] = True
            out["opportunite_type"] = "1n2_stats"
            out["opportunite_detail"] = computed["conseil"]
    else:
        apply_non_calculable_velora(
            out,
            "Statistiques insuffisantes (forme 5 matchs non disponible)",
        )
    return out
