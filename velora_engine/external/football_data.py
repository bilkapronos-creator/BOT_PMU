"""
Client football-data.org (plan gratuit) — enrichissement intel / Poisson.
En-tête : X-Auth-Token (variable FOOTBALL_DATA_API_KEY).
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API_BASE = "https://api.football-data.org/v4"
MIN_INTERVAL_SEC = 6.5
COMPETITION_CODES = ("WC", "EC", "CL", "PL", "SA", "BL1", "PD", "FL1", "DED", "PPL", "ELC")

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_ALIASES_PATH = _DATA_DIR / "fd_team_aliases.json"
_INDEX_PATH = _DATA_DIR / "fd_team_index.json"

_last_request_ts = 0.0
_team_index: dict[str, dict[str, Any]] | None = None
_match_cache: dict[int, list[dict]] = {}
_CACHE_DIR = _DATA_DIR / "fd_cache"
_CACHE_TTL_SEC = int(os.environ.get("VELORA_FD_CACHE_TTL", str(24 * 3600)))


def api_enabled() -> bool:
    return bool(os.environ.get("FOOTBALL_DATA_API_KEY", "").strip())


def _token() -> str | None:
    t = os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
    return t or None


def _normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9\s\-']", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _alias_lookup(norm: str) -> str | None:
    if not _ALIASES_PATH.is_file():
        return None
    try:
        aliases = json.loads(_ALIASES_PATH.read_text(encoding="utf-8"))
        if isinstance(aliases, dict):
            return aliases.get(norm)
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _api_get(path: str) -> dict[str, Any] | None:
    token = _token()
    if not token:
        return None
    global _last_request_ts
    wait = MIN_INTERVAL_SEC - (time.time() - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    url = f"{API_BASE}/{path.lstrip('/')}"
    req = urllib.request.Request(url, headers={"X-Auth-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            _last_request_ts = time.time()
            data = json.loads(resp.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
    except urllib.error.HTTPError as err:
        _last_request_ts = time.time()
        if err.code == 429:
            time.sleep(12)
        return None
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        _last_request_ts = time.time()
        return None


def _save_index(index: dict[str, dict[str, Any]]) -> None:
    try:
        _INDEX_PATH.write_text(
            json.dumps({"teams": index}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _load_index() -> dict[str, dict[str, Any]]:
    global _team_index
    if _team_index is not None:
        return _team_index
    index: dict[str, dict[str, Any]] = {}
    if _INDEX_PATH.is_file():
        try:
            raw = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
            teams = raw.get("teams") if isinstance(raw, dict) else None
            if isinstance(teams, dict):
                index = {str(k): v for k, v in teams.items() if isinstance(v, dict)}
        except (OSError, json.JSONDecodeError):
            index = {}
    _team_index = index
    return index


def bootstrap_team_index(*, force: bool = False) -> int:
    """Indexe les équipes des compétitions majeures (une fois ou si vide)."""
    if not api_enabled():
        return 0
    index = _load_index()
    if index and not force:
        return len(index)
    added = 0
    for code in COMPETITION_CODES:
        data = _api_get(f"competitions/{code}/teams")
        if not data:
            continue
        for team in data.get("teams") or []:
            if not isinstance(team, dict) or not team.get("id"):
                continue
            tid = int(team["id"])
            name = str(team.get("name") or "")
            short = str(team.get("shortName") or name)
            tla = str(team.get("tla") or "")
            entry = {"id": tid, "name": name, "shortName": short, "tla": tla}
            for key in {_normalize_name(name), _normalize_name(short), _normalize_name(tla)}:
                if key and key not in index:
                    index[key] = entry
                    added += 1
    _team_index = index
    if added:
        _save_index(index)
    return len(index)


def resolve_team_id(team_name: str) -> int | None:
    if not api_enabled():
        return None
    index = _load_index()
    if not index:
        bootstrap_team_index()
        index = _load_index()
    norm = _normalize_name(team_name)
    alias = _alias_lookup(norm)
    candidates = [norm]
    if alias:
        candidates.insert(0, _normalize_name(alias))
    for key in candidates:
        hit = index.get(key)
        if hit and hit.get("id"):
            return int(hit["id"])
    # sous-chaîne
    for key, hit in index.items():
        if not hit.get("id"):
            continue
        if key in norm or norm in key:
            return int(hit["id"])
        api_name = _normalize_name(hit.get("name") or "")
        if api_name and (api_name in norm or norm in api_name):
            return int(hit["id"])
    return None


def _result_char(goals_for: int, goals_against: int) -> str:
    if goals_for > goals_against:
        return "W"
    if goals_for < goals_against:
        return "L"
    return "D"


def _cache_path_team_matches(team_id: int) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"team_{team_id}_matches.json"


def _read_disk_match_cache(team_id: int) -> list[dict] | None:
    path = _cache_path_team_matches(team_id)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        if time.time() - float(raw.get("ts") or 0) > _CACHE_TTL_SEC:
            return None
        matches = raw.get("matches")
        return matches if isinstance(matches, list) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _write_disk_match_cache(team_id: int, matches: list[dict]) -> None:
    path = _cache_path_team_matches(team_id)
    try:
        path.write_text(
            json.dumps({"ts": time.time(), "matches": matches}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def fetch_team_recent_matches(team_id: int, *, limit: int = 5) -> list[dict]:
    if team_id in _match_cache:
        return _match_cache[team_id]
    disk = _read_disk_match_cache(team_id)
    if disk is not None:
        _match_cache[team_id] = disk
        return disk
    data = _api_get(f"teams/{team_id}/matches?status=FINISHED&limit={limit}")
    matches = []
    if data and isinstance(data.get("matches"), list):
        matches = [m for m in data["matches"] if isinstance(m, dict)]
    _match_cache[team_id] = matches
    if matches:
        _write_disk_match_cache(team_id, matches)
    return matches


def team_form_from_matches(team_id: int, matches: list[dict]) -> dict[str, Any]:
    letters: list[str] = []
    goals_for = goals_against = 0
    played = 0
    for m in matches:
        sc = (m.get("score") or {}).get("fullTime") or {}
        h = sc.get("home")
        a = sc.get("away")
        if h is None or a is None:
            continue
        home_id = (m.get("homeTeam") or {}).get("id")
        is_home = int(home_id or 0) == int(team_id)
        gf = int(h) if is_home else int(a)
        ga = int(a) if is_home else int(h)
        letters.append(_result_char(gf, ga))
        goals_for += gf
        goals_against += ga
        played += 1
    from velora_intel import parse_form_string

    form = parse_form_string("".join(letters[:5]))
    avg_gf = round(goals_for / played, 2) if played else None
    avg_ga = round(goals_against / played, 2) if played else None
    return {
        "played": played,
        "goals_for_avg": avg_gf,
        "goals_against_avg": avg_ga,
        "form": form,
        "raw": form.get("raw") or "",
    }


def enrich_intel_from_football_data(
    intel: dict[str, Any],
    *,
    home: str,
    away: str,
) -> dict[str, Any]:
    """Fusionne stats football-data.org dans le bloc intel (sans écraser si déjà riche)."""
    if not api_enabled():
        return intel
    out = dict(intel or {})
    home_id = resolve_team_id(home)
    away_id = resolve_team_id(away)
    if not home_id and not away_id:
        out["fd_available"] = False
        return out

    fd_meta: dict[str, Any] = {"home_id": home_id, "away_id": away_id}
    hf_played = int((out.get("home_form") or {}).get("played") or 0)
    af_played = int((out.get("away_form") or {}).get("played") or 0)
    fetch_goals = os.environ.get("VELORA_FD_GOALS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )

    if home_id and (hf_played < 3 or fetch_goals):
        hm = fetch_team_recent_matches(home_id)
        hs = team_form_from_matches(home_id, hm)
        fd_meta["home"] = hs
        if hs.get("played", 0) >= 2:
            if hf_played < 3:
                out["home_form"] = hs["form"]
            if fetch_goals and hs.get("goals_for_avg") is not None:
                out["fd_home_goals_for"] = hs["goals_for_avg"]
                out["fd_home_goals_against"] = hs["goals_against_avg"]
    if away_id and (af_played < 3 or fetch_goals):
        am = fetch_team_recent_matches(away_id)
        ast = team_form_from_matches(away_id, am)
        fd_meta["away"] = ast
        if ast.get("played", 0) >= 2:
            if af_played < 3:
                out["away_form"] = ast["form"]
            if fetch_goals and ast.get("goals_for_avg") is not None:
                out["fd_away_goals_for"] = ast["goals_for_avg"]
                out["fd_away_goals_against"] = ast["goals_against_avg"]

    hf = out.get("home_form") or {}
    af = out.get("away_form") or {}
    if int(hf.get("played") or 0) >= 2 and int(af.get("played") or 0) >= 2:
        out["has_form"] = True
        out["form_edge"] = int(hf.get("points") or 0) - int(af.get("points") or 0)

    out["fd_available"] = bool(home_id or away_id)
    out["football_data"] = fd_meta
    return out
