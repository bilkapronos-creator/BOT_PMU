"""
Plan C — scores finaux via Playwright (SofaScore → Flashscore → Google).
Utilisé quand Winamax et TheSportsDB n'ont pas de résultat.
"""
from __future__ import annotations

import os
import re
import time
import urllib.parse
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from foot_team_fuzzy import normalize_team, score_foot_plausible, text_mentions_both_teams

TZ_PARIS = ZoneInfo("Europe/Paris")
SCORE_RE = re.compile(
    r"\b(\d{1,2})\s*[-–:]\s*(\d{1,2})\b",
)
HEADLESS = os.environ.get("VELORA_SCRAPER_HEADLESS", "1").strip() not in ("0", "false", "no")
PAUSE_SEC = float(os.environ.get("VELORA_SCRAPER_PAUSE", "1.2"))
PAGE_TIMEOUT_MS = int(os.environ.get("VELORA_SCRAPER_TIMEOUT_MS", "35000"))

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    _HAS_PLAYWRIGHT = True
except ImportError:
    PlaywrightTimeout = Exception  # type: ignore[misc, assignment]
    sync_playwright = None  # type: ignore[assignment]
    _HAS_PLAYWRIGHT = False

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _date_fr(kickoff: datetime | None) -> str:
    kickoff = kickoff or datetime.now(tz=TZ_PARIS)
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=TZ_PARIS)
    return kickoff.astimezone(TZ_PARIS).strftime("%d %B %Y").replace(
        "January", "janvier"
    ).replace("February", "février").replace("March", "mars").replace(
        "April", "avril"
    ).replace("May", "mai").replace("June", "juin").replace(
        "July", "juillet"
    ).replace("August", "août").replace("September", "septembre").replace(
        "October", "octobre"
    ).replace("November", "novembre").replace("December", "décembre")


def _date_short(kickoff: datetime | None) -> str:
    kickoff = kickoff or datetime.now(tz=TZ_PARIS)
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=TZ_PARIS)
    return kickoff.astimezone(TZ_PARIS).strftime("%d/%m/%Y")


def _score_valide(dom: int, ext: int) -> bool:
    return score_foot_plausible(dom, ext)


def _extraire_score_du_texte(
    text: str,
    home: str,
    away: str,
) -> dict[str, int] | None:
    """Cherche un score près des lignes mentionnant les deux équipes."""
    if not text or not text_mentions_both_teams(text, home, away, threshold=0.55):
        return None

    candidats: list[tuple[int, int, int]] = []  # (dom, ext, priorité — plus bas = mieux)

    def _ajouter(dom: int, ext: int, prio: int) -> None:
        if _score_valide(dom, ext):
            candidats.append((dom, ext, prio))

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        if not text_mentions_both_teams(line, home, away, threshold=0.55):
            continue
        window = "\n".join(lines[max(0, i - 2) : i + 4])
        low = window.lower()
        prio = 0 if any(k in low for k in ("ft", "termin", "final", "fin ")) else 2
        for m in SCORE_RE.finditer(window):
            _ajouter(int(m.group(1)), int(m.group(2)), prio)

    h_norm, a_norm = normalize_team(home), normalize_team(away)
    h_keys = [p for p in h_norm.split() if len(p) >= 4] or [h_norm[:5]]
    a_keys = [p for p in a_norm.split() if len(p) >= 4] or [a_norm[:5]]
    for m in SCORE_RE.finditer(text):
        dom, ext = int(m.group(1)), int(m.group(2))
        if not _score_valide(dom, ext):
            continue
        start = max(0, m.start() - 400)
        end = min(len(text), m.end() + 400)
        ctx = text[start:end].lower()
        if any(k in ctx for k in h_keys) and any(k in ctx for k in a_keys):
            prio = 1 if any(k in ctx for k in ("ft", "termin", "final")) else 3
            _ajouter(dom, ext, prio)

    if not candidats:
        return None
    # Priorité FT, puis total de buts le plus bas (évite 19-15, 2-12)
    candidats.sort(key=lambda t: (t[2], t[0] + t[1]))
    dom, ext, _ = candidats[0]
    return {"domicile": dom, "exterieur": ext}


def _goto_safe(page, url: str) -> bool:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        time.sleep(PAUSE_SEC)
        return True
    except Exception as exc:
        print(f"[foot-scraper] Navigation échouée ({url[:60]}…) : {exc}")
        return False


def _try_sofascore(page, home: str, away: str, kickoff: datetime | None) -> dict[str, int] | None:
    q = urllib.parse.quote(f"{home} {away}")
    url = f"https://www.sofascore.com/search?q={q}"
    if not _goto_safe(page, url):
        return None
    try:
        page.wait_for_selector("a[href*='/football/match/']", timeout=12_000)
    except Exception:
        pass
    text = page.inner_text("body")
    score = _extraire_score_du_texte(text, home, away)
    if score:
        print(f"[foot-scraper] SofaScore : {home} {score['domicile']}-{score['exterieur']} {away}")
    return score


def _try_flashscore(page, home: str, away: str, kickoff: datetime | None) -> dict[str, int] | None:
    q = urllib.parse.quote(f"{home} {away}")
    for url in (
        f"https://www.flashscore.fr/recherche/?q={q}",
        f"https://www.flashscore.com/search/?q={q}",
    ):
        if not _goto_safe(page, url):
            continue
        text = page.inner_text("body")
        score = _extraire_score_du_texte(text, home, away)
        if score:
            print(
                f"[foot-scraper] Flashscore : {home} {score['domicile']}-{score['exterieur']} {away}"
            )
            return score
    return None


def _try_google(page, home: str, away: str, kickoff: datetime | None) -> dict[str, int] | None:
    date_fr = _date_fr(kickoff)
    date_short = _date_short(kickoff)
    queries = [
        f"Score {home} vs {away} {date_fr}",
        f"résultat {home} {away} {date_short}",
        f"{home} {away} score football",
    ]
    for q in queries:
        url = "https://www.google.com/search?" + urllib.parse.urlencode(
            {"q": q, "hl": "fr"}
        )
        if not _goto_safe(page, url):
            continue
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        text = page.inner_text("body")
        score = _extraire_score_du_texte(text, home, away)
        if score:
            print(f"[foot-scraper] Google : {home} {score['domicile']}-{score['exterieur']} {away}")
            return score
    return None


def fetch_score_playwright(
    equipe_domicile: str,
    equipe_exterieur: str,
    kickoff: datetime | None = None,
    page: Any = None,
) -> dict[str, int] | None:
    """
    Un match : SofaScore → Flashscore → Google.
    Si page Playwright fournie, réutilise la session (batch).
    """
    if not _HAS_PLAYWRIGHT:
        print("[foot-scraper] Playwright non installé — plan C ignoré.")
        return None

    own_browser = page is None
    if own_browser:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=HEADLESS,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            context = browser.new_context(
                user_agent=UA,
                locale="fr-FR",
                timezone_id="Europe/Paris",
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()
            page.set_default_timeout(PAGE_TIMEOUT_MS)
            try:
                return _fetch_one_page(page, equipe_domicile, equipe_exterieur, kickoff)
            finally:
                context.close()
                browser.close()
    return _fetch_one_page(page, equipe_domicile, equipe_exterieur, kickoff)


def _fetch_one_page(
    page: Any,
    home: str,
    away: str,
    kickoff: datetime | None,
) -> dict[str, int] | None:
    for fn in (_try_sofascore, _try_flashscore, _try_google):
        try:
            score = fn(page, home, away, kickoff)
            if score:
                return score
        except PlaywrightTimeout:
            print(f"[foot-scraper] Timeout {fn.__name__} ({home} — {away})")
        except Exception as exc:
            print(f"[foot-scraper] {fn.__name__} : {exc}")
    return None


def fetch_scores_playwright_batch(
    matchs: list[dict],
) -> dict[str, dict[str, int]]:
    """
    Plusieurs matchs en une session Playwright.
    matchs : [{ id_match, equipe_domicile, equipe_exterieur, kickoff? }]
    """
    if not matchs or not _HAS_PLAYWRIGHT:
        return {}

    out: dict[str, dict[str, int]] = {}
    print(f"[foot-scraper] Plan C Playwright : {len(matchs)} match(s)…")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            user_agent=UA,
            locale="fr-FR",
            timezone_id="Europe/Paris",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        page.set_default_timeout(PAGE_TIMEOUT_MS)
        try:
            for m in matchs:
                mid = str(m.get("id_match") or "").strip()
                home = str(m.get("equipe_domicile") or "")
                away = str(m.get("equipe_exterieur") or "")
                if not mid or not home or not away:
                    continue
                kickoff = m.get("kickoff")
                score = _fetch_one_page(page, home, away, kickoff)
                if score:
                    out[mid] = score
                time.sleep(PAUSE_SEC * 0.5)
        finally:
            context.close()
            browser.close()

    print(f"[foot-scraper] Plan C : {len(out)}/{len(matchs)} score(s) récupéré(s)")
    return out
