"""Utilitaires lecture PRELOADED_STATE Winamax (partagés scraper / extractor)."""

from __future__ import annotations

import getpass
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

OU_MAX_COTE_SANE = 5.0


def proxy_user_data_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / ".velora_proxy_profile"


def proxy_interactive_enabled() -> bool:
    """Auth proxy via fenêtre Chromium (dialogue identifiant / mot de passe)."""
    flag = os.environ.get("VELORA_PROXY_INTERACTIVE", "").strip().lower()
    if flag in ("0", "false", "no"):
        return False
    if flag in ("1", "true", "yes"):
        return True
    if os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true":
        return False
    if os.environ.get("CI", "").strip().lower() in ("1", "true"):
        return False
    proxy_url = os.environ.get("VELORA_PROXY_URL", "").strip()
    if not proxy_url:
        return False
    # Identifiants déjà dans l'URL ou VELORA_PROXY_USER/PASS → auth Playwright auto
    _, user, pwd = _proxy_url_parts(proxy_url)
    if user and pwd:
        return False
    return True


def _proxy_url_parts(proxy_url: str) -> tuple[str, str | None, str | None]:
    raw = (proxy_url or "").strip()
    if not raw:
        return "", None, None
    parsed = urlparse(raw)
    if not parsed.hostname:
        return raw, None, None
    scheme = parsed.scheme or "http"
    port = parsed.port or 6645
    server = f"{scheme}://{parsed.hostname}:{port}"
    user = unquote(parsed.username) if parsed.username else None
    pwd = unquote(parsed.password) if parsed.password else None
    user = user or os.environ.get("VELORA_PROXY_USER", "").strip() or None
    pwd = pwd or os.environ.get("VELORA_PROXY_PASS", "").strip() or None
    return server, user, pwd


def resolve_playwright_proxy_config() -> dict[str, str] | None:
    """
    Config proxy Playwright. En mode interactif, demande login/mot de passe
    dans le terminal si absents de l'URL / .env.
    """
    proxy_url = os.environ.get("VELORA_PROXY_URL", "").strip()
    if not proxy_url:
        return None
    server, user, pwd = _proxy_url_parts(proxy_url)
    if proxy_interactive_enabled() and sys.stdin.isatty():
        if not user:
            try:
                user = input("[proxy] Login proxy : ").strip() or None
            except EOFError:
                user = None
        if not pwd:
            try:
                pwd = getpass.getpass("[proxy] Mot de passe proxy : ").strip() or None
            except EOFError:
                pwd = None
    out: dict[str, str] = {"server": server}
    if user:
        out["username"] = user
    if pwd:
        out["password"] = pwd
    return out


def playwright_proxy_from_url(proxy_url: str, *, interactive: bool | None = None) -> dict[str, str] | None:
    """
    Format Playwright : server + username/password séparés.
    Évite ERR_INVALID_AUTH_CREDENTIALS quand user:pass est dans l'URL.
    """
    server, user, pwd = _proxy_url_parts(proxy_url)
    if not server:
        return None
    use_interactive = proxy_interactive_enabled() if interactive is None else interactive
    if use_interactive:
        return {"server": server}
    out: dict[str, str] = {"server": server}
    if user:
        out["username"] = user
    if pwd:
        out["password"] = pwd
    return out


def wait_for_proxy_authentication(page, test_url: str, *, label: str = "winamax_dump") -> None:
    """
    Ouvre Winamax et attend que l'utilisateur se connecte au proxy
    (dialogue Chromium identifiant / mot de passe).
    """
    print(f"\n[{label}] " + "=" * 56)
    print(f"[{label}] Vérification connexion proxy → Winamax")
    print(f"[{label}] La fenêtre Chromium doit afficher Winamax (pas d'erreur proxy).")
    print(f"[{label}] " + "=" * 56 + "\n")
    try:
        page.goto(test_url, wait_until="domcontentloaded", timeout=120_000)
    except Exception as exc:
        print(f"[{label}] Navigation initiale (proxy) : {exc}", file=sys.stderr)
    if sys.stdin.isatty():
        try:
            input(f"[{label}] >>> Appuyez sur ENTRÉE une fois le proxy connecté… ")
        except EOFError:
            time.sleep(20)
    else:
        print(f"[{label}] Mode non interactif — pause 25s pour auth proxy…")
        time.sleep(25)


def lookup(mapping: dict, key: Any) -> dict | None:
    try:
        val = mapping.get(str(key)) or mapping.get(key)
        return val if isinstance(val, dict) else None
    except Exception:
        return None


def lookup_odd(odds: dict | None, outcome_id: Any) -> float | None:
    if not odds:
        return None
    try:
        val = odds.get(str(outcome_id)) or odds.get(outcome_id)
        if isinstance(val, dict):
            val = val.get("odds") or val.get("price")
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def bets_for_match(bets: dict, match_id: Any) -> list[dict]:
    out: list[dict] = []
    if not isinstance(bets, dict):
        return out
    mid = str(match_id)
    for bet in bets.values():
        if not isinstance(bet, dict):
            continue
        if str(bet.get("matchId")) == mid:
            out.append(bet)
    return out


def safe_float(val: Any) -> float | None:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def bet_label(bet: dict) -> str:
    parts = [
        bet.get("betTypeName"),
        bet.get("betTitle"),
        bet.get("betFilterName"),
        bet.get("specialBetValue"),
    ]
    return " ".join(str(p) for p in parts if p).strip().lower()


def outcome_pct(out: dict) -> int | None:
    try:
        prob = out.get("percentDistribution") or out.get("probability")
        if prob is None:
            return None
        p = float(prob)
        if 0 < p <= 1:
            return int(round(p * 100))
        return int(round(p))
    except (TypeError, ValueError):
        return None


def sanitize_ou_cote(price: float | None, max_cote: float = OU_MAX_COTE_SANE) -> float | None:
    if price is None:
        return None
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p > max_cote or p < 1.01:
        return None
    return round(p, 2)


def parse_ou_side_line(label: str) -> tuple[str | None, str | None]:
    """Retourne (plus|moins, ligne) — lignes dynamiques ex. 0.5, 1.5, 2.5, 3.5."""
    try:
        lab = str(label or "").lower().replace(",", ".")
        m = re.search(r"(plus|moins)\s*(?:de\s*)?(\d+(?:\.\d+)?)", lab)
        if not m:
            return None, None
        side = m.group(1)
        line = m.group(2)
        if side in ("plus", "moins") and line:
            return side, line
    except Exception:
        pass
    return None, None


def find_raw_match(state: dict | None, match_id: Any) -> dict | None:
    if not state or not isinstance(state, dict):
        return None
    matches = state.get("matches") or {}
    if not isinstance(matches, dict):
        return None
    key = str(match_id)
    if key in matches and isinstance(matches[key], dict):
        return matches[key]
    try:
        kid = int(match_id)
        if kid in matches and isinstance(matches[kid], dict):
            return matches[kid]
    except (TypeError, ValueError):
        pass
    for m in matches.values():
        if isinstance(m, dict) and str(m.get("matchId")) == key:
            return m
    return None


def resolve_category_name(state: dict, category_id: Any) -> str | None:
    cats = state.get("categories") or state.get("category") or {}
    if not isinstance(cats, dict):
        return None
    row = lookup(cats, category_id)
    if not row:
        return None
    for key in ("categoryName", "name", "label", "title"):
        val = row.get(key)
        if val:
            return str(val).strip()
    return None


def resolve_tournament_name(state: dict, tournament_id: Any) -> str | None:
    tours = state.get("tournaments") or state.get("tournament") or {}
    if not isinstance(tours, dict):
        return None
    row = lookup(tours, tournament_id)
    if not row:
        return None
    for key in ("tournamentName", "name", "label", "title"):
        val = row.get(key)
        if val:
            return str(val).strip()
    return None
