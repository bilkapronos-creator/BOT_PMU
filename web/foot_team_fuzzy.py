"""
Comparaison tolérante des noms d'équipes (Palmeiras ≈ SE Palmeiras).
Utilise difflib ; thefuzz/rapidfuzz en option si installé.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

try:
    from thefuzz import fuzz  # type: ignore[import-untyped]

    _HAS_THEFUZZ = True
except ImportError:
    try:
        from rapidfuzz import fuzz  # type: ignore[import-untyped]

        _HAS_THEFUZZ = True
    except ImportError:
        _HAS_THEFUZZ = False

_STOP_WORDS = frozenset(
    {
        "fc",
        "cf",
        "sc",
        "ac",
        "as",
        "us",
        "ud",
        "cd",
        "rc",
        "real",
        "de",
        "la",
        "le",
        "les",
        "the",
        "ca",
        "sv",
        "se",
        "cf",
        "universidad",
        "université",
        "atletico",
        "atlético",
        "athletic",
    }
)


def normalize_team(name: str) -> str:
    s = str(name or "").lower().strip()
    s = s.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
    s = s.replace("ù", "u").replace("ô", "o").replace("î", "i").replace("ç", "c")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    tokens = [t for t in s.split() if t and t not in _STOP_WORDS]
    return " ".join(tokens) if tokens else s


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.95
    if _HAS_THEFUZZ:
        return max(
            fuzz.ratio(a, b) / 100.0,
            fuzz.partial_ratio(a, b) / 100.0,
            fuzz.token_sort_ratio(a, b) / 100.0,
        )
    return SequenceMatcher(None, a, b).ratio()


def team_similarity(name_a: str, name_b: str) -> float:
    """Score 0–1 entre deux libellés d'équipe."""
    return _ratio(normalize_team(name_a), normalize_team(name_b))


def teams_pair_match(
    home_a: str,
    away_a: str,
    home_b: str,
    away_b: str,
    *,
    threshold: float = 0.68,
) -> tuple[bool, float]:
    """
    True si les paires domicile/extérieur correspondent (ordre direct ou inversé).
    Retourne (match_ok, score_confiance 0–2).
    """
    h1, a1 = normalize_team(home_a), normalize_team(away_a)
    h2, a2 = normalize_team(home_b), normalize_team(away_b)
    if not h1 or not a1 or not h2 or not a2:
        return False, 0.0

    direct = _ratio(h1, h2) + _ratio(a1, a2)
    croise = _ratio(h1, a2) + _ratio(a1, h2)
    best = max(direct, croise)
    ok = best >= threshold * 2
    return ok, best


def text_mentions_both_teams(text: str, home: str, away: str, *, threshold: float = 0.68) -> bool:
    """True si le texte contient probablement les deux équipes."""
    blob = normalize_team(text)
    h, a = normalize_team(home), normalize_team(away)
    if not blob or not h or not a:
        return False
    if h in blob and a in blob:
        return True
    # tokens significatifs (≥ 4 car.)
    for th in (h, a):
        parts = [p for p in th.split() if len(p) >= 4]
        if parts and not any(p in blob for p in parts):
            return False
    return team_similarity(home, text) >= threshold or team_similarity(away, text) >= threshold
