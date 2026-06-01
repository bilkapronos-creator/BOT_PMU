"""Modificateurs de confiance — enjeu compétition + alertes pro."""

from __future__ import annotations

from velora_engine.models import CompetitionMeta, ConfidenceBlock, ConfidenceModifier, ProAlert

STAKES_PENALTY_FRIENDLY = -0.30
STAKES_PENALTY_CUP_EARLY = -0.20
ROTATION_PENALTY = -0.15

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
)


def _is_early_cup_round(round_label: str | None) -> bool:
    if not round_label:
        return False
    low = round_label.lower()
    return any(h in low for h in EARLY_CUP_ROUND_HINTS)


def stakes_modifiers(competition: CompetitionMeta) -> list[ConfidenceModifier]:
    mods: list[ConfidenceModifier] = []
    if competition.type == "friendly":
        mods.append(
            ConfidenceModifier(
                "friendly_match",
                STAKES_PENALTY_FRIENDLY,
                "Match amical — confiance réduite",
            )
        )
    elif competition.type == "cup" and _is_early_cup_round(competition.round):
        mods.append(
            ConfidenceModifier(
                "cup_early_round",
                STAKES_PENALTY_CUP_EARLY,
                "Coupe (tour précoce) — rotation probable",
            )
        )
    return mods


def build_confidence(
    *,
    velora_score: float | None,
    indice_velora: int,
    indice_label: str | None,
    competition: CompetitionMeta,
    pro_alerts: list[ProAlert],
    best_edge: float | None = None,
) -> ConfidenceBlock:
    if velora_score is not None:
        base = min(0.95, max(0.05, velora_score / 100.0))
    elif best_edge is not None:
        base = min(0.85, max(0.35, (best_edge - 1.0) * 0.65 + 0.45))
    else:
        base = 0.50

    modifiers = stakes_modifiers(competition)
    for alert in pro_alerts:
        if alert.type == "rotation_risk":
            modifiers.append(
                ConfidenceModifier(
                    "rotation_risk",
                    ROTATION_PENALTY,
                    alert.message[:56],
                )
            )

    adjusted = base
    for m in modifiers:
        adjusted += m.delta
    adjusted = max(0.05, min(0.95, adjusted))

    return ConfidenceBlock(
        velora_score=velora_score,
        indice_velora=indice_velora,
        indice_label=indice_label,
        base_confidence=round(base, 3),
        adjusted_confidence=round(adjusted, 3),
        modifiers=modifiers,
    )
