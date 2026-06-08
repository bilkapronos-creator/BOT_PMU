"""Réévalue les archives score exact (règle top 3) et régénère api_velora_communaute.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
sys.path.insert(0, str(WEB))

from publish_communaute import construire_bloc_foot  # noqa: E402
from velora_finance import fusionner_blocs_sports  # noqa: E402
from velora_archiver_foot import (  # noqa: E402
    ARCHIVES_FOOT_PATH,
    MATCHS_JSON_PATH,
    SNAPSHOT_PREMIUM_PATH,
    _ecrire_json,
    _index_archives,
    _lire_json,
    _revalider_archives_score_exact_top3,
)


def main() -> int:
    archives = _lire_json(ARCHIVES_FOOT_PATH, [])
    if not isinstance(archives, list):
        archives = []
    par_id = _index_archives(archives)
    snapshots = _lire_json(SNAPSHOT_PREMIUM_PATH, [])
    catalogue = _lire_json(MATCHS_JSON_PATH, [])

    n = _revalider_archives_score_exact_top3(par_id, snapshots, catalogue)
    finales = sorted(
        par_id.values(),
        key=lambda a: a.get("match_start_ts") or a.get("timestamp") or 0,
        reverse=True,
    )
    _ecrire_json(ARCHIVES_FOOT_PATH, finales)

    out = WEB / "api_velora_communaute.json"
    existant: dict = {}
    if out.is_file():
        try:
            existant = json.loads(out.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existant = {}
    foot_bloc = construire_bloc_foot()
    pmu_bloc = existant.get("pmu") or {}
    communaute = fusionner_blocs_sports(pmu_bloc, foot_bloc)
    meta = existant.get("meta") or {}
    meta["genere_at"] = __import__("datetime").datetime.now(
        tz=__import__("datetime").timezone.utc,
    ).isoformat()
    meta["reval_score_exact_top3"] = n
    communaute["meta"] = meta
    out.write_text(json.dumps(communaute, ensure_ascii=False, indent=2), encoding="utf-8")

    se = (communaute.get("foot") or {}).get("detail_par_marche", {}).get("score_exact", {})
    wins = [
        a for a in finales
        if str(a.get("marche") or "").lower() == "score_exact" and a.get("reussi_foot")
    ]
    print(f"[reval] {n} archive(s) score exact réévaluée(s)")
    print(f"[reval] Score exact communauté : {se.get('succes', 0)}/{se.get('total', 0)} ({se.get('taux', 0)}%)")
    for w in wins:
        print(
            f"  OK {w.get('equipe_domicile')} - {w.get('equipe_exterieur')} "
            f"{w.get('score_final')} -> {w.get('type_pari_foot')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
