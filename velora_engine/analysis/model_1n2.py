"""
Probabilités 1N2 « modèle Velora » — gratuit (cotes implicites + forme / classement / H2H).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from velora_engine.analysis.probability import implied_probabilities
from velora_intel import _pick_1n2_from_signals, intel_stats_suffisantes


def _normalize_pct(raw: dict[str, float]) -> dict[str, int]:
    total = sum(max(0.0, float(raw.get(k) or 0)) for k in ("1", "N", "2"))
    if total <= 0:
        return {"1": 33, "N": 34, "2": 33}
    scaled = {k: 100.0 * max(0.0, float(raw.get(k) or 0)) / total for k in ("1", "N", "2")}
    out = {k: int(round(scaled[k])) for k in ("1", "N", "2")}
    diff = 100 - sum(out.values())
    if diff:
        kmax = max(out, key=out.get)
        out[kmax] += diff
    return out


def _signal_weights_from_intel(
    implied: dict[str, int],
    intel: dict[str, Any] | None,
) -> dict[str, float]:
    intel = intel or {}
    pick, _margin = _pick_1n2_from_signals(
        implied,
        int(intel.get("form_edge") or 0),
        int(intel.get("rank_edge") or 0),
        intel.get("h2h"),
    )
    p1 = float(implied.get("1") or 0)
    pn = float(implied.get("N") or 0)
    p2 = float(implied.get("2") or 0)
    form_edge = int(intel.get("form_edge") or 0)
    rank_edge = int(intel.get("rank_edge") or 0)
    h2h = intel.get("h2h") or {}
    h2h_home = int(h2h.get("home_wins") or 0)
    h2h_away = int(h2h.get("away_wins") or 0)

    w1 = p1 * 0.12 + form_edge * 3.0 + rank_edge * 2.5 + h2h_home * 3.5
    wn = pn * 0.10 + max(0, 10 - abs(form_edge)) * 0.5
    w2 = p2 * 0.12 - form_edge * 3.0 - rank_edge * 2.5 + h2h_away * 3.5
    weights = {"1": max(1.0, w1 + 8.0), "N": max(1.0, wn + 6.0), "2": max(1.0, w2 + 8.0)}
    # Léger bonus sur le pick stats pour orienter le blend
    weights[pick] = weights.get(pick, 1.0) * 1.12
    return weights


def blend_probabilities_1n2(
    cotes: dict[str, float | None],
    intel: dict[str, Any] | None = None,
    *,
    implied_weight: float = 0.55,
) -> tuple[dict[str, int], dict[str, int]]:
    """Retourne (probas_modèle, probas_marché implicites)."""
    marche = implied_probabilities(cotes)
    weights = _signal_weights_from_intel(marche, intel)
    blend: dict[str, float] = {}
    iw = min(0.85, max(0.35, float(implied_weight)))
    for key in ("1", "N", "2"):
        signal_pct = 100.0 * weights[key] / sum(weights.values())
        blend[key] = iw * float(marche[key]) + (1.0 - iw) * signal_pct
    return _normalize_pct(blend), marche


def pronostic_pick_from_intel(
    implied: dict[str, int],
    intel: dict[str, Any] | None,
) -> str:
    intel = intel or {}
    pick, _ = _pick_1n2_from_signals(
        implied,
        int(intel.get("form_edge") or 0),
        int(intel.get("rank_edge") or 0),
        intel.get("h2h"),
    )
    return pick


def pronostic_label_for_pick(pick: str, home: str, away: str) -> str:
    labels = {
        "1": f"Victoire {home}".strip() or "Victoire domicile",
        "N": "Match nul",
        "2": f"Victoire {away}".strip() or "Victoire extérieur",
    }
    return labels.get(pick, pick)


def confiance_niveau_from_context(
    intel: dict[str, Any] | None,
    *,
    indice_velora: int = 0,
    adjusted_confidence: float | None = None,
    friendly: bool = False,
) -> str:
    if friendly or not intel_stats_suffisantes(intel or {}):
        return "faible"
    adj = adjusted_confidence
    if adj is not None and adj < 0.38:
        return "faible"
    if indice_velora >= 3 and (adj is None or adj >= 0.48):
        return "haute"
    if indice_velora >= 2:
        return "moyenne"
    return "faible"


@dataclass
class Analyse1n2Context:
    probabilites_modele: dict[str, int]
    probabilites_marche: dict[str, int]
    pronostic_1n2: str
    pronostic_label: str
    confiance_niveau: str
    line_signal: str | None = None


def build_1n2_analysis_context(
    *,
    cotes: dict[str, float | None],
    intel: dict[str, Any] | None,
    home: str,
    away: str,
    indice_velora: int = 0,
    adjusted_confidence: float | None = None,
    friendly: bool = False,
    line_signal: str | None = None,
) -> Analyse1n2Context:
    modele, marche = blend_probabilities_1n2(cotes, intel)
    pick = pronostic_pick_from_intel(marche, intel)
    return Analyse1n2Context(
        probabilites_modele=modele,
        probabilites_marche=marche,
        pronostic_1n2=pick,
        pronostic_label=pronostic_label_for_pick(pick, home, away),
        confiance_niveau=confiance_niveau_from_context(
            intel,
            indice_velora=indice_velora,
            adjusted_confidence=adjusted_confidence,
            friendly=friendly,
        ),
        line_signal=line_signal,
    )
