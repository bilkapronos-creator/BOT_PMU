"""
Modèles de données Velora Engine v2 — api_velora_matchs.json

Sérialisation JSON via `to_json_dict()` / `document_to_json()`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from velora_engine.config import ENGINE_ID, SCHEMA_VERSION


def _drop_none(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _drop_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_drop_none(x) for x in obj]
    return obj


@dataclass
class OuLine:
    plus_cote: float | None = None
    moins_cote: float | None = None
    plus_prob: int | None = None
    moins_prob: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(asdict(self))


@dataclass
class TeamGoalsSide:
    team_name: str
    lines: dict[str, OuLine] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_name": self.team_name,
            "lines": {k: v.to_dict() for k, v in self.lines.items()},
        }


@dataclass
class MarketsRaw:
    over_under_total: dict[str, OuLine] = field(default_factory=dict)
    btts: dict[str, float | None] | None = None
    team_goals: dict[str, TeamGoalsSide] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "over_under_total": {
                k: v.to_dict() for k, v in self.over_under_total.items()
            },
            "team_goals": {k: v.to_dict() for k, v in self.team_goals.items()},
        }
        if self.btts is not None:
            out["btts"] = _drop_none(self.btts)
        return out


@dataclass
class CompetitionMeta:
    name: str
    type: str  # league | cup | friendly | international | other
    round: str | None = None
    stakes_tier: str = "medium"  # high | medium | low
    category_id: str | None = None
    tournament_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(asdict(self))


@dataclass
class MetaMatch:
    competition: CompetitionMeta

    def to_dict(self) -> dict[str, Any]:
        return {"competition": self.competition.to_dict()}


@dataclass
class ConfidenceModifier:
    code: str
    delta: float
    label: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConfidenceBlock:
    velora_score: float | None = None
    indice_velora: int = 0
    indice_label: str | None = None
    base_confidence: float | None = None
    adjusted_confidence: float | None = None
    modifiers: list[ConfidenceModifier] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "velora_score": self.velora_score,
                "indice_velora": self.indice_velora,
                "indice_label": self.indice_label,
                "base_confidence": self.base_confidence,
                "adjusted_confidence": self.adjusted_confidence,
                "modifiers": [m.to_dict() for m in self.modifiers],
            }
        )


@dataclass
class ProAlert:
    type: str
    severity: str  # low | medium | high
    message: str
    team: str | None = None
    suggested_pick: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(asdict(self))


@dataclass
class ValueBet:
    market: str
    pick: str
    label: str
    cote: float | None
    edge: float
    stars: int = 3
    is_primary: bool = False
    line: str | None = None
    side: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(asdict(self))


@dataclass
class PrimaryPick:
    market: str
    pick: str
    label: str
    cote: float | None
    conseil_short: str

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(asdict(self))


@dataclass
class FreeAnalysis:
    cotes_1n2: dict[str, float | None]
    probabilites: dict[str, int]
    markets_raw: MarketsRaw
    value_bets: list[ValueBet] = field(default_factory=list)
    primary_pick: PrimaryPick | None = None
    display_badges: list[str] = field(default_factory=list)
    probabilites_marche: dict[str, int] | None = None
    pronostic_1n2: str | None = None
    pronostic_label: str | None = None
    confiance_niveau: str | None = None
    line_signal: str | None = None
    poisson_lambdas: dict[str, float] | None = None
    top_scores_modele: list[dict[str, Any]] | None = None
    prob_over_25_modele: int | None = None
    prob_btts_modele: int | None = None
    football_data_enriched: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "cotes_1n2": self.cotes_1n2,
                "probabilites": self.probabilites,
                "probabilites_marche": self.probabilites_marche,
                "pronostic_1n2": self.pronostic_1n2,
                "pronostic_label": self.pronostic_label,
                "confiance_niveau": self.confiance_niveau,
                "line_signal": self.line_signal,
                "poisson_lambdas": self.poisson_lambdas,
                "top_scores_modele": self.top_scores_modele,
                "prob_over_25_modele": self.prob_over_25_modele,
                "prob_btts_modele": self.prob_btts_modele,
                "football_data_enriched": self.football_data_enriched,
                "markets_raw": self.markets_raw.to_dict(),
                "value_bets": [v.to_dict() for v in self.value_bets],
                "primary_pick": self.primary_pick.to_dict()
                if self.primary_pick
                else None,
                "display_badges": self.display_badges,
            }
        )


@dataclass
class ScoreExactRow:
    score: str
    prob: int | None = None
    cote: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(asdict(self))


@dataclass
class ScorerRow:
    joueur: str
    cote: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PremiumScorerMarket:
    top: list[ScorerRow] = field(default_factory=list)
    value_bet: ValueBet | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "top": [r.to_dict() for r in self.top],
                "value_bet": self.value_bet.to_dict() if self.value_bet else None,
            }
        )


@dataclass
class PremiumScoreExact:
    top3: list[ScoreExactRow] = field(default_factory=list)
    value_bet: ValueBet | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "top3": [r.to_dict() for r in self.top3],
                "value_bet": self.value_bet.to_dict() if self.value_bet else None,
            }
        )


@dataclass
class PremiumAnalysis:
    score_exact: PremiumScoreExact = field(default_factory=PremiumScoreExact)
    buteur_match: PremiumScorerMarket = field(default_factory=PremiumScorerMarket)
    buteur_mi_temps: PremiumScorerMarket = field(default_factory=PremiumScorerMarket)
    buteur_double: PremiumScorerMarket = field(default_factory=PremiumScorerMarket)
    value_bets: list[ValueBet] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score_exact": self.score_exact.to_dict(),
            "buteur_match": self.buteur_match.to_dict(),
            "buteur_mi_temps": self.buteur_mi_temps.to_dict(),
            "buteur_double": self.buteur_double.to_dict(),
            "value_bets": [v.to_dict() for v in self.value_bets],
        }


@dataclass
class LegacyShim:
    """Transition front v1 — TTL 2 semaines max."""

    conseil: str | None = None
    tendance_buts: str | None = None
    marches_supplementaires: dict[str, Any] | None = None
    opportunite_type: str | None = None
    is_opportunite: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(asdict(self))


@dataclass
class MatchRecordV2:
    id_match: str
    date_match: str
    match_start_ts: int | None
    match_status: str
    equipe_domicile: str
    equipe_exterieur: str
    meta_match: MetaMatch
    confidence: ConfidenceBlock
    pro_alerts: list[ProAlert]
    free_analysis: FreeAnalysis
    premium_analysis: PremiumAnalysis
    legacy: LegacyShim | None = None

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "id_match": self.id_match,
            "date_match": self.date_match,
            "match_start_ts": self.match_start_ts,
            "match_status": self.match_status,
            "equipe_domicile": self.equipe_domicile,
            "equipe_exterieur": self.equipe_exterieur,
            "meta_match": self.meta_match.to_dict(),
            "confidence": self.confidence.to_dict(),
            "pro_alerts": [a.to_dict() for a in self.pro_alerts],
            "free_analysis": self.free_analysis.to_dict(),
            "premium_analysis": self.premium_analysis.to_dict(),
        }
        if self.legacy is not None:
            body["_legacy"] = self.legacy.to_dict()
        return body


@dataclass
class ApiVeloraDocument:
    schema_version: int
    meta: dict[str, Any]
    matchs: list[MatchRecordV2]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "meta": self.meta,
            "matchs": [m.to_dict() for m in self.matchs],
        }


def build_example_document() -> ApiVeloraDocument:
    """Fixture réaliste pour validation produit (B1)."""
    ou_25 = OuLine(plus_cote=1.85, moins_cote=1.95, plus_prob=54, moins_prob=46)
    ou_15 = OuLine(plus_cote=1.28, moins_cote=3.50, plus_prob=72, moins_prob=28)
    markets = MarketsRaw(
        over_under_total={"1.5": ou_15, "2.5": ou_25, "3.5": OuLine(plus_cote=2.90, moins_cote=1.40)},
        btts={"oui": 1.70, "non": 2.10},
        team_goals={
            "home": TeamGoalsSide(
                team_name="CODM Meknès",
                lines={"1.5": OuLine(plus_cote=2.10, moins_cote=1.65, plus_prob=42)},
            ),
            "away": TeamGoalsSide(
                team_name="Olympique Dcheira",
                lines={"0.5": OuLine(plus_cote=1.55, moins_cote=2.35)},
            ),
        },
    )
    free_vb_1n2 = ValueBet(
        market="1n2",
        pick="1",
        label="Victoire CODM Meknès",
        cote=1.96,
        edge=1.59,
        stars=5,
        is_primary=True,
    )
    free = FreeAnalysis(
        cotes_1n2={"1": 1.96, "N": 3.20, "2": 4.10},
        probabilites={"1": 81, "N": 15, "2": 4},
        markets_raw=markets,
        value_bets=[free_vb_1n2],
        primary_pick=PrimaryPick(
            market="1n2",
            pick="1",
            label="Victoire CODM Meknès",
            cote=1.96,
            conseil_short="Value Bet — Victoire domicile",
        ),
        display_badges=[],
    )
    premium_vb_score = ValueBet(
        market="score_exact",
        pick="2-1",
        label="Score exact 2-1",
        cote=9.50,
        edge=1.18,
        stars=4,
    )
    premium = PremiumAnalysis(
        score_exact=PremiumScoreExact(
            top3=[
                ScoreExactRow("2-1", 14, 9.50),
                ScoreExactRow("1-0", 11, 7.20),
                ScoreExactRow("1-1", 9, 6.80),
            ],
            value_bet=premium_vb_score,
        ),
        buteur_match=PremiumScorerMarket(
            top=[
                ScorerRow("A. Diallo", 4.50),
                ScorerRow("M. El Amrani", 5.20),
            ],
        ),
        buteur_mi_temps=PremiumScorerMarket(top=[ScorerRow("A. Diallo", 6.00)]),
        buteur_double=PremiumScorerMarket(
            top=[ScorerRow("Diallo + El Amrani", 12.00)]
        ),
        value_bets=[premium_vb_score],
    )
    match = MatchRecordV2(
        id_match="EXAMPLE_MEKNES_001",
        date_match="01/06/2026 à 20:00",
        match_start_ts=1780341600,
        match_status="PREMATCH",
        equipe_domicile="CODM Meknès",
        equipe_exterieur="Olympique Dcheira",
        meta_match=MetaMatch(
            CompetitionMeta(
                name="Botola Pro",
                type="league",
                round=None,
                stakes_tier="medium",
            )
        ),
        confidence=ConfidenceBlock(
            velora_score=None,
            indice_velora=0,
            indice_label="Non calculable",
            base_confidence=0.72,
            adjusted_confidence=0.72,
            modifiers=[],
        ),
        pro_alerts=[],
        free_analysis=free,
        premium_analysis=premium,
        legacy=LegacyShim(
            conseil="🔥 Value Bet Détecté : Dom",
            tendance_buts="Match Offensif",
            marches_supplementaires={
                "plus_moins_buts": {"2.5": ou_25.to_dict()},
            },
        ),
    )
    friendly = MatchRecordV2(
        id_match="EXAMPLE_FRIENDLY_002",
        date_match="02/06/2026 à 18:00",
        match_start_ts=1780428000,
        match_status="PREMATCH",
        equipe_domicile="PSG",
        equipe_exterieur="Amiens SC",
        meta_match=MetaMatch(
            CompetitionMeta(
                name="Club Friendlies",
                type="friendly",
                stakes_tier="low",
            )
        ),
        confidence=ConfidenceBlock(
            velora_score=78.0,
            indice_velora=4,
            base_confidence=0.85,
            adjusted_confidence=0.55,
            modifiers=[
                ConfidenceModifier(
                    "friendly_match",
                    -0.30,
                    "Match amical — confiance réduite",
                ),
            ],
        ),
        pro_alerts=[
            ProAlert(
                type="rotation_risk",
                severity="high",
                team="PSG",
                message="Ligue des Champions dans 4 jours — risque de rotation sur le favori",
                suggested_pick={"market": "1n2", "pick": "dc_x2"},
            )
        ],
        free_analysis=FreeAnalysis(
            cotes_1n2={"1": 1.22, "N": 6.50, "2": 11.00},
            probabilites={"1": 88, "N": 8, "2": 4},
            markets_raw=MarketsRaw(
                over_under_total={"2.5": OuLine(plus_cote=1.45, moins_cote=2.65)},
                btts={"oui": 1.55, "non": 2.35},
            ),
            value_bets=[
                ValueBet(
                    market="ou_total",
                    pick="plus",
                    label="Plus de 2,5 buts",
                    cote=1.45,
                    edge=1.08,
                    stars=4,
                    line="2.5",
                    side="plus",
                    is_primary=True,
                )
            ],
            primary_pick=PrimaryPick(
                market="ou_total",
                pick="plus",
                label="Plus de 2,5 buts",
                cote=1.45,
                conseil_short="Value Bet — Over 2,5 buts",
            ),
            display_badges=["Plus de 2,5 buts"],
        ),
        premium_analysis=PremiumAnalysis(),
    )
    return ApiVeloraDocument(
        schema_version=SCHEMA_VERSION,
        meta={
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "engine": ENGINE_ID,
            "match_count": 2,
            "note": "Fixture B1 — exemple schema v2",
        },
        matchs=[match, friendly],
    )


def document_to_json(doc: ApiVeloraDocument, indent: int = 2) -> str:
    import json

    return json.dumps(doc.to_dict(), ensure_ascii=False, indent=indent) + "\n"
