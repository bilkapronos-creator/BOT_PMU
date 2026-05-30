"""Génère api_velora_communaute.json (PMU archives + Foot archivés)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from velora_finance import MISE_UNITAIRE, bloc_communaute_depuis_stats, fusionner_blocs_sports
from stats_foot import get_stats_foot_publiques
from stats_pmu import get_stats_publiques

OUT = Path(__file__).resolve().parent / "api_velora_communaute.json"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    pmu = get_stats_publiques()
    foot = get_stats_foot_publiques()
    pmu_bloc = bloc_communaute_depuis_stats(
        {
            "taux": pmu.get("taux") or pmu.get("taux_reussite_plateforme", 0),
            "victoires": pmu.get("victoires") or pmu.get("victoires_plateforme", 0),
            "total": pmu.get("total") or pmu.get("courses_terminees_plateforme", 0),
            "mises_cumulees": pmu.get("mises_cumulees", 0),
            "profit_net": pmu.get("profit_net", 0),
            "roi_pct": pmu.get("roi_pct", 0),
        }
    )
    foot_bloc = bloc_communaute_depuis_stats(foot)
    data = fusionner_blocs_sports(pmu_bloc, foot_bloc)
    data["meta"] = {
        "mise_unitaire": MISE_UNITAIRE,
        "genere_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[communaute] → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
