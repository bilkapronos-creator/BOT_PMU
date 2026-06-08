"""Copie les JSON racine vers web/ + snapshot cotes + calibration Foot."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))


def _sanitize_json_file(path: Path) -> None:
    from velora_engine.analysis.match_scores import sanitize_matchs_document

    raw = json.loads(path.read_text(encoding="utf-8"))
    clean = sanitize_matchs_document(raw)
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    for name in ("api_velora_matchs.json", "api_velora_premium.json"):
        src = ROOT / name
        if src.is_file():
            _sanitize_json_file(src)
            shutil.copy2(src, WEB / name)
            print(f"[sync] {name} (scores alignés) -> web/")

    try:
        from velora_engine.odds_snapshots import snapshot_from_json_file

        matchs = WEB / "api_velora_matchs.json"
        hist = WEB / "velora_odds_history.json"
        if matchs.is_file():
            snapshot_from_json_file(matchs, hist)
            print(f"[sync] historique cotes -> {hist.name}")
    except Exception as err:
        print(f"[sync] historique cotes ignoré ({err})")

    try:
        from stats_foot import ecrire_calibration_foot

        ecrire_calibration_foot()
        print("[sync] velora_foot_calibration.json régénéré")
    except Exception as err:
        print(f"[sync] calibration ignorée ({err})")

    try:
        from velora_archiver_foot import resoudre_matchs_en_attente

        stats = resoudre_matchs_en_attente(assurer_premium=True)
        print(
            f"[sync] archives Foot résolues "
            f"({stats.get('resolus', 0)} validés, {stats.get('encore_attente', 0)} en attente)"
        )
    except Exception as err:
        print(f"[sync] résolution Foot ignorée ({err})")

    try:
        from publish_communaute import main as publish_communaute

        publish_communaute()
        print("[sync] api_velora_communaute.json régénéré")
    except Exception as err:
        print(f"[sync] publication communauté ignorée ({err})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
