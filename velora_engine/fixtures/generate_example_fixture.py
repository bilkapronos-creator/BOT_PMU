#!/usr/bin/env python3
"""Génère velora_engine/fixtures/api_velora_matchs_v2.example.json (B1)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from velora_engine.models import build_example_document, document_to_json  # noqa: E402

OUT = Path(__file__).resolve().parent / "api_velora_matchs_v2.example.json"


def main() -> None:
    doc = build_example_document()
    OUT.write_text(document_to_json(doc), encoding="utf-8")
    print(f"[fixture] Écrit {OUT} ({OUT.stat().st_size} octets)")


if __name__ == "__main__":
    main()
