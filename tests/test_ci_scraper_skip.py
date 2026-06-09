"""Tests skip proactif CI + proxy interactif."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

TZ = ZoneInfo("Europe/Paris")


def _match_row(kickoff: datetime, home: str = "A") -> dict:
    kickoff = kickoff.astimezone(TZ)
    return {
        "id_match": "1",
        "date_match": kickoff.strftime("%d/%m/%Y à %H:%M"),
        "match_start_ts": int(kickoff.timestamp()),
        "equipe_domicile": home,
        "equipe_exterieur": "B",
    }


def test_proxy_interactive_desactive_si_identifiants_url(monkeypatch):
    monkeypatch.delenv("VELORA_PROXY_INTERACTIVE", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("VELORA_PROXY_URL", "http://user:secret@1.2.3.4:6645")

    from velora_engine.scrape.winamax_state import proxy_interactive_enabled

    assert proxy_interactive_enabled() is False


def test_proxy_interactive_sans_identifiants(monkeypatch):
    monkeypatch.delenv("VELORA_PROXY_INTERACTIVE", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("VELORA_PROXY_URL", "http://1.2.3.4:6645")

    from velora_engine.scrape.winamax_state import proxy_interactive_enabled

    assert proxy_interactive_enabled() is True


def test_json_skip_scraper_ci_exige_journee_et_fichier_recent(tmp_path, monkeypatch):
    import run_all

    now = datetime.now(tz=TZ)
    hier = now - timedelta(days=1)
    fichier = tmp_path / "premium.json"
    fichier.write_text(
        json.dumps([_match_row(hier.replace(hour=15, minute=0))], ensure_ascii=False),
        encoding="utf-8",
    )
    old = time.time() - 2 * 3600
    os.utime(fichier, (old, old))
    monkeypatch.setenv("VELORA_CI_SCRAPER_MAX_AGE_H", "6")

    assert run_all._json_skip_scraper_ci(fichier, 6.0) is False

    kick = now.replace(hour=18, minute=0, second=0, microsecond=0)
    if kick <= now:
        kick += timedelta(hours=2)
    aujourd_hui = tmp_path / "auj.json"
    aujourd_hui.write_text(
        json.dumps([_match_row(kick)], ensure_ascii=False),
        encoding="utf-8",
    )
    recent = time.time() - 1800
    os.utime(aujourd_hui, (recent, recent))

    assert run_all._json_skip_scraper_ci(aujourd_hui, 6.0) is True
