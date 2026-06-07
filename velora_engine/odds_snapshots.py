"""
Historique gratuit des cotes Winamax — détection de line movement entre runs.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

MAX_ENTRIES_PER_MATCH = 24
MIN_MOVE_PCT = 2.5


def default_history_path() -> Path:
    return Path(__file__).resolve().parents[1] / "web" / "velora_odds_history.json"


def load_odds_history(path: Path | None = None) -> dict[str, Any]:
    return _load_raw(path or default_history_path())


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1, "by_match": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("by_match"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "by_match": {}}


def _matchs_from_doc(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [m for m in data if isinstance(m, dict)]
    if isinstance(data, dict) and isinstance(data.get("matchs"), list):
        return [m for m in data["matchs"] if isinstance(m, dict)]
    return []


def _cotes_from_match(m: dict) -> dict[str, float | None]:
    free = m.get("free_analysis") if isinstance(m.get("free_analysis"), dict) else {}
    c = free.get("cotes_1n2") if isinstance(free.get("cotes_1n2"), dict) else m.get("cotes")
    if not isinstance(c, dict):
        return {"1": None, "N": None, "2": None}
    return {
        "1": c.get("1"),
        "N": c.get("N"),
        "2": c.get("2"),
    }


def line_signal_for_pick(
    history: dict[str, Any],
    match_id: str,
    pick: str,
) -> str | None:
    """cote_baisse | cote_hausse | stable | None si pas d'historique."""
    pick = str(pick or "").strip()
    if pick not in ("1", "N", "2"):
        return None
    entries = (history.get("by_match") or {}).get(str(match_id)) or []
    if len(entries) < 2:
        return None
    prev = entries[-2].get("cotes") or {}
    cur = entries[-1].get("cotes") or {}
    try:
        old = float(prev.get(pick))
        new = float(cur.get(pick))
    except (TypeError, ValueError):
        return None
    if old <= 1.0 or new <= 1.0:
        return None
    delta_pct = (old - new) / old * 100.0
    if delta_pct >= MIN_MOVE_PCT:
        return "cote_baisse"
    if delta_pct <= -MIN_MOVE_PCT:
        return "cote_hausse"
    return "stable"


def append_odds_snapshot(path: Path, matchs: list[dict]) -> dict[str, Any]:
    """Ajoute un snapshot horodaté ; retourne l'historique mis à jour."""
    store = _load_raw(path)
    by_match: dict[str, list] = store.setdefault("by_match", {})
    ts = int(time.time())
    for m in matchs:
        mid = str(m.get("id_match") or "").strip()
        if not mid:
            continue
        cotes = _cotes_from_match(m)
        if not any(cotes.get(k) for k in ("1", "N", "2")):
            continue
        bucket = by_match.setdefault(mid, [])
        bucket.append({"ts": ts, "cotes": cotes})
        if len(bucket) > MAX_ENTRIES_PER_MATCH:
            by_match[mid] = bucket[-MAX_ENTRIES_PER_MATCH:]
    store["updated_at"] = ts
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    return store


def enrich_matchs_line_signals(
    matchs: list[dict],
    history: dict[str, Any],
) -> None:
    """Renseigne free_analysis.line_signal sur chaque match (mutate in place)."""
    for m in matchs:
        free = m.get("free_analysis")
        if not isinstance(free, dict):
            continue
        pick = free.get("pronostic_1n2") or m.get("velora_pick_1n2")
        mid = str(m.get("id_match") or "")
        sig = line_signal_for_pick(history, mid, str(pick or ""))
        if sig:
            free["line_signal"] = sig


def snapshot_from_json_file(json_path: Path, history_path: Path) -> dict[str, Any]:
    if not json_path.is_file():
        return _load_raw(history_path)
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _load_raw(history_path)
    matchs = _matchs_from_doc(data)
    history = _load_raw(history_path)
    enrich_matchs_line_signals(matchs, history)
    return append_odds_snapshot(history_path, matchs)
