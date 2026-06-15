"""Recalcule conseils_intelligents / conseil racine dans api_velora_matchs.json (moteur bet_advisor)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from velora_engine.analysis.bet_advisor import build_intelligent_conseils
from velora_engine.models import MarketOutcome, MarketsRaw, OuLine, PremiumAnalysis, ValueBet


def markets_from_raw(raw: dict | None) -> MarketsRaw:
    raw = raw or {}
    ou: dict[str, OuLine] = {}
    for line, val in (raw.get("over_under_total") or {}).items():
        if isinstance(val, dict):
            ou[line] = OuLine(
                plus_cote=val.get("plus_cote"),
                moins_cote=val.get("moins_cote"),
                plus_prob=val.get("plus_prob"),
                moins_prob=val.get("moins_prob"),
            )
    dc: dict[str, MarketOutcome] = {}
    for pick, val in (raw.get("double_chance") or {}).items():
        if isinstance(val, dict):
            dc[str(pick).lower()] = MarketOutcome(
                cote=val.get("cote"),
                prob=val.get("prob"),
            )
    return MarketsRaw(over_under_total=ou, double_chance=dc)


def value_bets_from_fa(fa: dict) -> list[ValueBet]:
    out: list[ValueBet] = []
    for vb in fa.get("value_bets") or []:
        if not isinstance(vb, dict):
            continue
        out.append(
            ValueBet(
                market=str(vb.get("market") or ""),
                pick=str(vb.get("pick") or ""),
                label=str(vb.get("label") or ""),
                cote=vb.get("cote"),
                edge=vb.get("edge"),
                stars=int(vb.get("stars") or 0),
            )
        )
    return out


def patch_match(m: dict) -> bool:
    fa = m.get("free_analysis")
    if not isinstance(fa, dict):
        return False
    cotes = fa.get("cotes_1n2") or {}
    probs = fa.get("probabilites") or {}
    if not any(cotes.get(k) for k in ("1", "N", "2")):
        return False
    pick = fa.get("pronostic_1n2") or m.get("velora_pick_1n2")
    ldm = m.get("les_deux_marquent")
    les_deux = ldm if isinstance(ldm, int) else None
    conseils, meilleur = build_intelligent_conseils(
        cotes_1n2=cotes,
        probs=probs,
        markets=markets_from_raw(fa.get("markets_raw")),
        les_deux_marquent=les_deux,
        prob_over_25_modele=fa.get("prob_over_25_modele"),
        prob_btts_modele=fa.get("prob_btts_modele"),
        home=str(m.get("equipe_domicile") or ""),
        away=str(m.get("equipe_exterieur") or ""),
        value_bets=value_bets_from_fa(fa),
        premium=PremiumAnalysis(),
        pronostic_1n2=str(pick or "").strip() or None,
    )
    fa["conseils_intelligents"] = [c.to_dict() for c in conseils]
    fa["meilleur_conseil"] = meilleur.to_dict() if meilleur else None
    label = str(fa.get("pronostic_label") or "").strip()
    if meilleur and meilleur.label:
        label = meilleur.label
    if label:
        m["conseil"] = label
        leg = m.get("_legacy")
        if isinstance(leg, dict):
            leg["conseil"] = label
    return True


def main() -> int:
    path = ROOT / "web" / "api_velora_matchs.json"
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    data = json.loads(path.read_text(encoding="utf-8"))
    matchs = data.get("matchs") or []
    n = sum(1 for m in matchs if isinstance(m, dict) and patch_match(m))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[rebuild_conseils] {n} match(s) -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
