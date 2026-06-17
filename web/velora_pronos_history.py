"""
Historique des pronostics Velora Foot — conserve les picks au fil des runs CI
pour reconstituer les archives des jours passés (matchs absents du catalogue actuel).
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

from velora_archiver_foot import (
    _est_archive_terminee_validee,
    _est_pronostic_velora,
    _id_match_base,
    _kickoff_match,
    _lire_resultat_pour_match,
    _marche_effectif,
    _match_deja_joue,
    _match_record_pour_id,
    _storage_key_archive,
    valider_foot,
)

TZ_PARIS = ZoneInfo("Europe/Paris")
ROOT = Path(__file__).resolve().parent
DEFAULT_PATH = ROOT / "velora_pronos_history.json"
MAX_SNAPSHOTS_PER_MATCH = 12


def default_history_path() -> Path:
    return DEFAULT_PATH


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


def _compact_snapshot(match: dict, ts: int) -> dict[str, Any]:
    free = match.get("free_analysis") if isinstance(match.get("free_analysis"), dict) else {}
    marche = _marche_effectif(match)
    snap: dict[str, Any] = {
        "ts": ts,
        "conseil": match.get("conseil") or "",
        "opportunite_detail": match.get("opportunite_detail") or match.get("conseil") or "",
        "opportunite_type": match.get("opportunite_type") or marche,
        "value_bet_type": match.get("value_bet_type"),
        "marche": marche,
        "cotes": match.get("cotes") or free.get("cotes_1n2") or {},
        "pronostic_1n2": free.get("pronostic_1n2") or match.get("pronostic_1n2"),
        "pronostic_label": free.get("pronostic_label") or match.get("pronostic_label"),
        "primary_pick": free.get("primary_pick") or match.get("primary_pick"),
        "velora_score": match.get("velora_score"),
        "indice_velora": match.get("indice_velora"),
        "is_opportunite": match.get("is_opportunite"),
        "date_match": match.get("date_match"),
    }
    if free:
        snap["free_analysis"] = free
    prem = match.get("premium_analysis")
    if isinstance(prem, dict):
        snap["premium_analysis"] = prem
    if match.get("scores_proposes"):
        snap["scores_proposes"] = match.get("scores_proposes")
    if match.get("score_exact"):
        snap["score_exact"] = match.get("score_exact")
    return snap


def append_pronos_snapshot(path: Path, matchs: list[dict]) -> dict[str, Any]:
    """Enregistre les pronos Velora du run (catalogue + premium)."""
    store = _load_raw(path)
    by_match: dict[str, Any] = store.setdefault("by_match", {})
    ts = int(time.time())
    n = 0
    for m in matchs:
        if not _est_pronostic_velora(m):
            continue
        mid = str(m.get("id_match") or "").strip()
        if not mid:
            continue
        entry = by_match.get(mid)
        if not isinstance(entry, dict):
            entry = {
                "id_match": mid,
                "equipe_domicile": m.get("equipe_domicile") or "?",
                "equipe_exterieur": m.get("equipe_exterieur") or "?",
                "match_start_ts": m.get("match_start_ts"),
                "date_match": m.get("date_match") or "",
                "snapshots": [],
            }
            by_match[mid] = entry
        else:
            if m.get("equipe_domicile"):
                entry["equipe_domicile"] = m.get("equipe_domicile")
            if m.get("equipe_exterieur"):
                entry["equipe_exterieur"] = m.get("equipe_exterieur")
            if m.get("match_start_ts"):
                entry["match_start_ts"] = m.get("match_start_ts")
            if m.get("date_match"):
                entry["date_match"] = m.get("date_match")

        snaps = entry.setdefault("snapshots", [])
        row = _compact_snapshot(m, ts)
        snaps.append(row)
        if len(snaps) > MAX_SNAPSHOTS_PER_MATCH:
            entry["snapshots"] = snaps[-MAX_SNAPSHOTS_PER_MATCH:]
        n += 1

    store["updated_at"] = ts
    store["snapshot_count"] = n
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    return store


def snapshot_pronos_from_json_files(
    matchs_path: Path,
    premium_path: Path | None = None,
    history_path: Path | None = None,
) -> dict[str, Any]:
    """Fusionne matchs + premium puis historise les pronos."""
    history_path = history_path or default_history_path()
    matchs: list[dict] = []
    seen: set[str] = set()
    for path in (matchs_path, premium_path):
        if not path or not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for m in _matchs_from_doc(data):
            mid = str(m.get("id_match") or "").strip()
            if not mid or mid in seen:
                continue
            seen.add(mid)
            matchs.append(m)
    return append_pronos_snapshot(history_path, matchs)


def _best_snapshot(entry: dict) -> dict | None:
    snaps = entry.get("snapshots") or []
    if not snaps:
        return None
    kickoff = _kickoff_from_entry(entry)
    if kickoff is not None:
        kick_ts = int(kickoff.timestamp())
        before = [s for s in snaps if int(s.get("ts") or 0) <= kick_ts + 7200]
        if before:
            return before[-1]
    return snaps[-1]


def _kickoff_from_entry(entry: dict) -> datetime | None:
    raw = entry.get("match_start_ts")
    if raw is not None:
        try:
            ts = float(raw)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=TZ_PARIS)
        except (TypeError, ValueError, OSError, OverflowError):
            pass
    fake = {"date_match": entry.get("date_match"), "match_start_ts": entry.get("match_start_ts")}
    return _kickoff_match(fake)


def _match_from_history(entry: dict, snapshot: dict) -> dict:
    """Reconstruit un match minimal pour valider_foot."""
    free = snapshot.get("free_analysis")
    if not isinstance(free, dict):
        free = {}
    match: dict[str, Any] = {
        "id_match": entry.get("id_match"),
        "equipe_domicile": entry.get("equipe_domicile"),
        "equipe_exterieur": entry.get("equipe_exterieur"),
        "date_match": snapshot.get("date_match") or entry.get("date_match"),
        "match_start_ts": entry.get("match_start_ts"),
        "conseil": snapshot.get("conseil") or "",
        "opportunite_detail": snapshot.get("opportunite_detail") or snapshot.get("conseil") or "",
        "opportunite_type": snapshot.get("opportunite_type") or snapshot.get("marche"),
        "value_bet_type": snapshot.get("value_bet_type"),
        "cotes": snapshot.get("cotes") or {},
        "pronostic_1n2": snapshot.get("pronostic_1n2"),
        "pronostic_label": snapshot.get("pronostic_label"),
        "velora_score": snapshot.get("velora_score"),
        "indice_velora": snapshot.get("indice_velora"),
        "is_opportunite": snapshot.get("is_opportunite"),
        "free_analysis": dict(free),
        "primary_pick": snapshot.get("primary_pick"),
    }
    if snapshot.get("premium_analysis"):
        match["premium_analysis"] = snapshot.get("premium_analysis")
    if snapshot.get("scores_proposes"):
        match["scores_proposes"] = snapshot.get("scores_proposes")
    if snapshot.get("score_exact"):
        match["score_exact"] = snapshot.get("score_exact")
    if snapshot.get("primary_pick") and not match["free_analysis"].get("primary_pick"):
        match["free_analysis"]["primary_pick"] = snapshot.get("primary_pick")
    if snapshot.get("pronostic_1n2") and not match["free_analysis"].get("pronostic_1n2"):
        match["free_analysis"]["pronostic_1n2"] = snapshot.get("pronostic_1n2")
    return match


def _archive_validee_existe(par_id: dict[str, dict], base: str) -> bool:
    for key, arch in par_id.items():
        if key == base or _id_match_base(key) == base:
            if _est_archive_terminee_validee(arch):
                return True
    return False


def backfill_archives_from_pronos_history(
    par_id: dict[str, dict],
    resultats: dict,
    snapshots: list[dict],
    catalogue: list[dict],
    now: datetime | None = None,
    history_path: Path | None = None,
) -> int:
    """Valide les archives des matchs passés via l'historique des pronos + scores connus."""
    now = now or datetime.now(tz=TZ_PARIS)
    store = _load_raw(history_path or default_history_path())
    by_match = store.get("by_match") or {}
    if not isinstance(by_match, dict):
        return 0

    added = 0
    for mid, entry in by_match.items():
        if not isinstance(entry, dict):
            continue
        base = _id_match_base(str(mid))
        if not base:
            continue

        snapshot = _best_snapshot(entry)
        if not snapshot:
            continue

        hist_match = _match_from_history(entry, snapshot)
        if not _est_pronostic_velora(hist_match):
            continue

        match = _match_record_pour_id(base, snapshots, catalogue, hist_match)
        if _kickoff_match(match) is None and entry.get("match_start_ts"):
            match["match_start_ts"] = entry.get("match_start_ts")
        if not _match_deja_joue(match, now):
            continue

        res = _lire_resultat_pour_match(resultats, base)
        if res is None:
            continue

        if _archive_validee_existe(par_id, base):
            continue

        archive = valider_foot(match, res)
        if not archive or not _est_archive_terminee_validee(archive):
            continue

        ak = _storage_key_archive(archive) or base
        archive["archive_key"] = ak
        existant = par_id.get(ak)
        if existant and _est_archive_terminee_validee(existant):
            continue
        par_id[ak] = archive
        added += 1

    return added
