"""Bootstrap index équipes football-data.org (nécessite FOOTBALL_DATA_API_KEY)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from velora_engine.external.football_data import api_enabled, bootstrap_team_index


def main() -> int:
    if not api_enabled():
        print("[bootstrap] FOOTBALL_DATA_API_KEY absente — ignoré.")
        return 1
    n = bootstrap_team_index(force=True)
    print(f"[bootstrap] Index football-data : {n} entrées.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
