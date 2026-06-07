"""Tests football-data.org (mockés — pas d'appel réseau)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from velora_engine.external import football_data as fd


def test_resolve_team_via_alias_index():
    index = {
        "france": {"id": 773, "name": "France", "shortName": "France", "tla": "FRA"},
        "denmark": {"id": 782, "name": "Denmark", "shortName": "Denmark", "tla": "DEN"},
    }
    fd._team_index = index
    with patch.object(fd, "api_enabled", return_value=True):
        assert fd.resolve_team_id("France") == 773
        assert fd.resolve_team_id("Danemark") == 782
    fd._team_index = None


def test_enrich_intel_goals_averages():
    intel = {"has_form": False, "home_form": {}, "away_form": {}}
    fake_matches_home = [
        {
            "homeTeam": {"id": 773},
            "awayTeam": {"id": 1},
            "score": {"fullTime": {"home": 2, "away": 0}},
        },
        {
            "homeTeam": {"id": 773},
            "awayTeam": {"id": 2},
            "score": {"fullTime": {"home": 1, "away": 1}},
        },
    ]
    with patch.object(fd, "api_enabled", return_value=True), patch.object(
        fd, "resolve_team_id", side_effect=lambda n: 773 if "Fran" in n or "fran" in n.lower() else 782
    ), patch.object(fd, "fetch_team_recent_matches", return_value=fake_matches_home):
        out = fd.enrich_intel_from_football_data(intel, home="France", away="Denmark")
    assert out.get("fd_available") is True
    assert out.get("fd_home_goals_for") == 1.5
    assert out.get("has_form") is True
