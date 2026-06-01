"""
Extraction SSR Winamax : donnees embarquees dans le HTML (PRELOADED_STATE).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

URLS = (
    "https://www.winamax.fr/paris-sportifs/sports/1",
    "https://www.winamax.fr/paris-sportifs",
)
OUT = Path(__file__).resolve().parent / "dump_winamax_html.json"
DEBUG_HTML = Path(__file__).resolve().parent / "dump_winamax_debug.html"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
WAIT_STATE_MS = int(os.environ.get("VELORA_DUMP_WAIT_MS", "45000"))
EVALUATE_TIMEOUT_MS = int(os.environ.get("VELORA_DUMP_EVAL_MS", "20000"))
REGEX_MAX_SECONDS = int(os.environ.get("VELORA_DUMP_REGEX_SEC", "25"))
GOTO_TIMEOUT_MS = int(os.environ.get("VELORA_DUMP_GOTO_MS", "90000"))
HTTP_TIMEOUT_SEC = int(os.environ.get("VELORA_DUMP_HTTP_TIMEOUT", "35"))


class WinamaxDumpError(Exception):
    """Erreur métier — arrêt propre du script."""


_EVALUATE_JS = """() => {
    function findState(obj, depth) {
        if (!obj || depth > 10) return null;
        if (typeof obj !== 'object') return null;
        if (obj.matches && obj.outcomes) {
            const m = obj.matches;
            if (typeof m === 'object' && Object.keys(m).length > 0) return obj;
        }
        if (Array.isArray(obj)) {
            for (const item of obj.slice(0, 30)) {
                const f = findState(item, depth + 1);
                if (f) return f;
            }
            return null;
        }
        const keys = Object.keys(obj);
        for (let i = 0; i < keys.length && i < 80; i++) {
            try {
                const f = findState(obj[keys[i]], depth + 1);
                if (f) return f;
            } catch (e) {}
        }
        return null;
    }
    const names = [
        'PRELOADED_STATE', '__INITIAL_STATE__', '__PRELOADED_STATE__', '__NUXT__'
    ];
    for (const name of names) {
        const found = findState(window[name], 0);
        if (found) return { source: 'window.' + name, data: found };
    }
    return null;
}"""


def _strict_on_wait_timeout() -> bool:
    v = os.environ.get("VELORA_DUMP_STRICT_WAIT", "").strip().lower()
    if v in ("0", "false", "no"):
        return False
    if v in ("1", "true", "yes"):
        return True
    # Sur GitHub Actions : laisser evaluate + regex après timeout (géoblocage fréquent)
    if os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true":
        return False
    return os.environ.get("CI", "").strip().lower() in ("1", "true")


def _find_sport_state(data: object, depth: int = 0) -> dict | None:
    if depth > 12 or data is None:
        return None
    if isinstance(data, dict):
        matches = data.get("matches")
        outcomes = data.get("outcomes")
        if isinstance(matches, dict) and isinstance(outcomes, dict) and len(matches) > 0:
            return data
        for val in data.values():
            found = _find_sport_state(val, depth + 1)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data[:50]:
            found = _find_sport_state(item, depth + 1)
            if found is not None:
                return found
    return None


def _normalize_state(data: object) -> tuple[dict | None, str]:
    found = _find_sport_state(data)
    if found is not None:
        n = len(found.get("matches") or {})
        return found, f"état sport ({n} matchs)"
    return None, ""


def _extract_via_evaluate(page) -> tuple[object | None, str]:
    print(f"[winamax_dump] page.evaluate() (timeout {EVALUATE_TIMEOUT_MS}ms)...")
    try:
        result = page.evaluate(_EVALUATE_JS, timeout=EVALUATE_TIMEOUT_MS)
    except Exception as e:
        print(f"[winamax_dump] evaluate() échoué ou timeout: {e}", file=sys.stderr)
        return None, ""
    if result and isinstance(result.get("data"), dict):
        return result["data"], result.get("source", "page.evaluate")
    return None, ""


def _extract_json_object(text: str, start: int) -> str | None:
    opener = text[start]
    if opener not in "{[":
        return None
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _extract_via_regex(html: str, deadline: float) -> tuple[object | None, str]:
    patterns = [
        r"window\.PRELOADED_STATE\s*=\s*",
        r"PRELOADED_STATE\s*=\s*",
        r"window\.__INITIAL_STATE__\s*=\s*",
    ]

    def _timed_out() -> bool:
        return time.monotonic() >= deadline

    for pat in patterns:
        if _timed_out():
            print("[winamax_dump] regex: budget temps épuisé", file=sys.stderr)
            break
        for m in re.finditer(pat, html):
            if _timed_out():
                break
            pos = m.end()
            while pos < len(html) and html[pos] in " \t\r\n":
                pos += 1
            chunk = _extract_json_object(html, pos)
            if not chunk:
                continue
            try:
                data = json.loads(chunk)
                norm, label = _normalize_state(data)
                if norm is not None:
                    return norm, f"regex ({pat.strip()}) {label}"
            except json.JSONDecodeError:
                continue

    if _timed_out():
        return None, ""

    script_re = re.compile(r"<script[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
    for i, m in enumerate(script_re.finditer(html)):
        if i >= 40 or _timed_out():
            break
        body = m.group(1)
        if "matches" not in body or "outcomes" not in body:
            continue
        idx = body.find('"matches"')
        if idx < 0:
            continue
        for start in range(idx, max(0, idx - 5000), -1):
            if _timed_out():
                break
            if body[start] in "{[":
                chunk = _extract_json_object(body, start)
                if not chunk or len(chunk) < 500:
                    continue
                try:
                    data = json.loads(chunk)
                    norm, label = _normalize_state(data)
                    if norm is not None:
                        return norm, f"regex (<script>) {label}"
                except json.JSONDecodeError:
                    continue
    return None, ""


def _dismiss_cookie_banner(page) -> None:
    for sel in (
        "button:has-text('Accepter')",
        "button:has-text('Tout accepter')",
        'button:has-text("J\'accepte")',
        "#tarteaucitronAllAllowed",
        "#didomi-notice-agree-button",
        "[data-testid='accept-all']",
    ):
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1500):
                loc.click(timeout=2000)
                print(f"[winamax_dump] Bannière cookies : clic {sel!r}")
                time.sleep(1)
                return
        except Exception:
            continue


def _wait_for_sport_state(page) -> bool:
    try:
        page.wait_for_function(
            """() => {
                const s = window.PRELOADED_STATE;
                return s && s.matches && Object.keys(s.matches).length > 0;
            }""",
            timeout=WAIT_STATE_MS,
        )
        print(f"[winamax_dump] PRELOADED_STATE prêt (<{WAIT_STATE_MS}ms)")
        return True
    except Exception as e:
        print(
            f"[winamax_dump] wait_for_function timeout ({WAIT_STATE_MS}ms): {e}",
            file=sys.stderr,
        )
        return False


def _diagnose_html(html: str, page) -> None:
    try:
        title = page.title()
    except Exception:
        title = ""
    low = html.lower()
    flags = [t for t in ("preload", "matches", "captcha", "cloudflare", "geoloc", "connexion") if t in low]
    print(f"[winamax_dump] Diagnostic — titre: {title!r}, indices: {', '.join(flags) or 'aucun'}")


def _close_browser(browser, context) -> None:
    for name, obj in (("context", context), ("browser", browser)):
        if obj is None:
            continue
        try:
            obj.close()
            print(f"[winamax_dump] {name}.close() OK")
        except Exception as e:
            print(f"[winamax_dump] {name}.close() erreur: {e}", file=sys.stderr)


def _try_url(page, url: str, *, state_ready: bool) -> tuple[object | None, str]:
    print(f"[winamax_dump] Navigation -> {url}")
    captured: list[dict] = []

    def on_response(response) -> None:
        try:
            if response.status != 200 or "winamax" not in response.url.lower():
                return
            if "json" not in (response.headers.get("content-type") or "").lower():
                return
            norm, _ = _normalize_state(response.json())
            if norm is not None:
                captured.append(norm)
        except Exception:
            pass

    page.on("response", on_response)

    try:
        page.goto(url, timeout=GOTO_TIMEOUT_MS, wait_until="domcontentloaded")
    except Exception as e:
        print(f"[winamax_dump] ATTENTION chargement: {e}")

    _dismiss_cookie_banner(page)

    if not state_ready:
        state_ready = _wait_for_sport_state(page)

    if not state_ready and _strict_on_wait_timeout():
        raise WinamaxDumpError(
            f"PRELOADED_STATE indisponible après {WAIT_STATE_MS}ms sur {url}. "
            "Winamax n'a pas exposé les matchs (géoblocage, cookies, ou page vide). "
            "Essayez VELORA_PROXY_URL ou relancez plus tard."
        )

    time.sleep(1)

    if captured:
        n = len(captured[-1].get("matches") or {})
        return captured[-1], f"réponse réseau ({n} matchs)"

    data, source = _extract_via_evaluate(page)
    if data is not None:
        norm, label = _normalize_state(data)
        if norm is not None:
            return norm, f"{source} {label}"

    if not state_ready:
        raise WinamaxDumpError(
            f"État sport introuvable après timeout {WAIT_STATE_MS}ms (evaluate vide)."
        )

    print("[winamax_dump] fallback regex (limité)...")
    try:
        html = page.content()
    except Exception as e:
        raise WinamaxDumpError(f"Impossible de lire le HTML: {e}") from e
    print(f"[winamax_dump] Taille HTML: {len(html):,} caracteres")
    deadline = time.monotonic() + REGEX_MAX_SECONDS
    data, source = _extract_via_regex(html, deadline)
    if data is None:
        _diagnose_html(html, page)
        if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
            DEBUG_HTML.write_text(html, encoding="utf-8")
            print(f"[winamax_dump] HTML debug: {DEBUG_HTML}")
    return data, source


def _chromium_headless() -> bool:
    force = os.environ.get("VELORA_HEADLESS", "").strip().lower()
    if force in ("0", "false", "no"):
        return False
    if force in ("1", "true", "yes"):
        return True
    # GitHub Actions : headless=True → chromium-headless-shell (souvent absent sur le runner)
    if os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true":
        return False
    return os.environ.get("CI", "").strip().lower() in ("1", "true")


def _fail(msg: str, code: int = 1) -> None:
    print(f"[winamax_dump] ECHEC: {msg}", file=sys.stderr)
    OUT.write_text("{}", encoding="utf-8")
    sys.exit(code)


def _try_http_regex() -> tuple[dict | None, str]:
    """Tentative sans navigateur (utile avec proxy FR ou IP locale)."""
    import urllib.error
    import urllib.request

    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Referer": "https://www.winamax.fr/",
    }
    proxy_url = os.environ.get("VELORA_PROXY_URL", "").strip()
    handlers: list = []
    if proxy_url:
        handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    opener = urllib.request.build_opener(*handlers)

    for url in URLS:
        print(f"[winamax_dump] HTTP GET -> {url}")
        try:
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=HTTP_TIMEOUT_SEC) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as e:
            print(f"[winamax_dump] HTTP échec {url}: {e}", file=sys.stderr)
            continue
        print(f"[winamax_dump] HTTP {len(html):,} octets")
        deadline = time.monotonic() + REGEX_MAX_SECONDS
        data, source = _extract_via_regex(html, deadline)
        if data is None:
            continue
        norm, label = _normalize_state(data)
        if norm is not None:
            return norm, f"HTTP {url} ({source} {label})"
    return None, ""


def main() -> None:
    if os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true":
        if not os.environ.get("VELORA_PROXY_URL", "").strip():
            print(
                "[winamax_dump] CI sans VELORA_PROXY_URL : risque de géoblocage Winamax "
                "(runner hors France). Ajoutez un proxy FR en secret GitHub.",
                file=sys.stderr,
            )

    data, source = _try_http_regex()
    if data is not None:
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        n_matches = len(data.get("matches", {}))
        print("[winamax_dump] SUCCES (HTTP, sans Chromium)")
        print(f"  Source  : {source}")
        print(f"  Matchs  : {n_matches}")
        return

    print("[winamax_dump] Demarrage Chromium (SSR)...")
    headless = _chromium_headless()
    proxy_url = os.environ.get("VELORA_PROXY_URL", "").strip()
    launch_kwargs: dict[str, Any] = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    }
    if proxy_url:
        launch_kwargs["proxy"] = {"server": proxy_url}
        print(f"[winamax_dump] Proxy: {proxy_url}")

    browser = None
    context = None
    data = None
    source = ""

    try:
        with sync_playwright() as p:
            print(f"[winamax_dump] chromium headless={headless}")
            browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(
                user_agent=UA,
                locale="fr-FR",
                timezone_id="Europe/Paris",
                viewport={"width": 1920, "height": 1080},
                extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9"},
            )
            page = context.new_page()
            page.set_default_timeout(min(EVALUATE_TIMEOUT_MS, 60_000))

            last_err: WinamaxDumpError | None = None
            for url in URLS:
                try:
                    data, source = _try_url(page, url, state_ready=False)
                    if data is not None:
                        print(f"[winamax_dump] OK via {source}")
                        break
                except WinamaxDumpError as e:
                    last_err = e
                    print(f"[winamax_dump] {url} — {e}", file=sys.stderr)
                except Exception as e:
                    print(f"[winamax_dump] URL {url} ignorée: {e}", file=sys.stderr)

            if data is None and last_err is not None:
                raise last_err

    except WinamaxDumpError as e:
        _close_browser(browser, context)
        browser = context = None
        _fail(str(e))

    finally:
        _close_browser(browser, context)

    if data is None:
        _fail(
            "aucune donnée JSON sport extraite sur les URLs testées. "
            "Astuce CI: VELORA_PROXY_URL (proxy FR)."
        )

    norm, _ = _normalize_state(data)
    if norm is None:
        _fail("JSON trouvé mais sans matches/outcomes valides")
    data = norm

    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    n_matches = len(data.get("matches", {}))
    print("[winamax_dump] SUCCES")
    print(f"  Source  : {source}")
    print(f"  Fichier : {OUT}")
    print(f"  Matchs  : {n_matches}")


if __name__ == "__main__":
    try:
        main()
    except WinamaxDumpError as e:
        print(f"[winamax_dump] {e}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
