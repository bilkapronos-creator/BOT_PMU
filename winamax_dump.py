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

from velora_engine.scrape.winamax_state import (
    proxy_interactive_enabled,
    proxy_user_data_dir,
    resolve_playwright_proxy_config,
    wait_for_proxy_authentication,
)

URLS = (
    "https://www.winamax.fr/paris-sportifs/sports/1",
    "https://www.winamax.fr/paris-sportifs/sports/5",
    "https://www.winamax.fr/paris-sportifs",
)
TENNIS_SPORT_ID = 5
TENNIS_URL = URLS[1]
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
TENNIS_POLL_SEC = float(os.environ.get("VELORA_DUMP_TENNIS_POLL_SEC", "45"))


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


def _harvest_state_chunks_from_text(text: str) -> list[dict]:
    """Extrait des blocs matches/outcomes depuis Socket.IO ou JSON embarqué."""
    chunks: list[dict] = []
    if not text:
        return chunks
    stripped = text.strip()
    if stripped.startswith("42"):
        try:
            payload = json.loads(stripped[2:])
            if isinstance(payload, list):
                for item in payload:
                    found = _find_sport_state(item)
                    if found is not None:
                        chunks.append(found)
                    if isinstance(item, dict):
                        for key in ("result", "value", "data"):
                            nested = item.get(key)
                            found = _find_sport_state(nested)
                            if found is not None:
                                chunks.append(found)
        except json.JSONDecodeError:
            pass
    try:
        data = json.loads(stripped)
        found = _find_sport_state(data)
        if found is not None:
            chunks.append(found)
    except json.JSONDecodeError:
        pass
    if '"matches"' in text:
        for m in re.finditer(r'\{"matches"\s*:\s*\{', text):
            chunk_str = _extract_json_object(text, m.start())
            if not chunk_str:
                continue
            try:
                data = json.loads(chunk_str)
                found = _find_sport_state(data)
                if found is not None:
                    chunks.append(found)
            except json.JSONDecodeError:
                continue
    return chunks


def _poll_tennis_matches_in_page(page, max_seconds: float = TENNIS_POLL_SEC) -> int:
    """Winamax injecte le tennis via Socket.IO — attendre que PRELOADED_STATE se remplisse."""
    deadline = time.monotonic() + max_seconds
    last_n = 0
    while time.monotonic() < deadline:
        try:
            n = page.evaluate(
                f"""() => {{
                const s = window.PRELOADED_STATE;
                if (!s || !s.matches) return 0;
                return Object.values(s.matches).filter(
                    (m) => m && Number(m.sportId) === {TENNIS_SPORT_ID}
                ).length;
            }}"""
            )
            n = int(n or 0)
            if n > last_n:
                print(f"[winamax_dump] poll Socket.IO → PRELOADED_STATE tennis: {n}")
                last_n = n
            if n >= 3:
                return n
        except Exception:
            pass
        time.sleep(2.0)
    return last_n


def _click_tennis_nav(page) -> None:
    for sel in ("a[href*='/sports/5']", "a[href*='sports/5']"):
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=2000):
                loc.click(timeout=3000)
                print(f"[winamax_dump] Clic navigation tennis ({sel})")
                time.sleep(1.5)
                return
        except Exception:
            continue


def _extract_via_evaluate(page) -> tuple[object | None, str]:
    print(f"[winamax_dump] page.evaluate() (timeout {EVALUATE_TIMEOUT_MS}ms)...")
    try:
        result = page.evaluate(_EVALUATE_JS)
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


def _wait_for_sport_id_matches(page, sport_id: int) -> bool:
    try:
        page.wait_for_function(
            f"""() => {{
                const s = window.PRELOADED_STATE;
                if (!s || !s.matches) return false;
                return Object.values(s.matches).some(
                    (m) => m && Number(m.sportId) === {sport_id}
                );
            }}""",
            timeout=WAIT_STATE_MS,
        )
        print(f"[winamax_dump] PRELOADED_STATE sportId={sport_id} prêt (<{WAIT_STATE_MS}ms)")
        return True
    except Exception as e:
        print(
            f"[winamax_dump] wait sportId={sport_id} timeout ({WAIT_STATE_MS}ms): {e}",
            file=sys.stderr,
        )
        return False


def _scroll_to_load_matches(page, times: int = 5) -> None:
    for _ in range(times):
        try:
            page.evaluate("window.scrollBy(0, Math.max(600, window.innerHeight))")
            time.sleep(0.7)
        except Exception:
            break
    try:
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(0.4)
    except Exception:
        pass


def _filter_winamax_state_by_sport(state: dict, sport_id: int) -> dict | None:
    """Ne garde que les matchs (et bets/outcomes/odds liés) d'un sport."""
    matches_map = state.get("matches") or {}
    filtered_matches: dict = {}
    match_ids: set[int] = set()
    for key, match in matches_map.items():
        if not isinstance(match, dict) or match.get("sportId") != sport_id:
            continue
        filtered_matches[key] = match
        try:
            match_ids.add(int(match.get("matchId") or key))
        except (TypeError, ValueError):
            continue
    if not filtered_matches:
        return None

    filtered_bets: dict = {}
    outcome_ids: set[int] = set()
    for bid, bet in (state.get("bets") or {}).items():
        if not isinstance(bet, dict):
            continue
        try:
            mid = int(bet.get("matchId") or 0)
        except (TypeError, ValueError):
            continue
        if mid not in match_ids:
            continue
        filtered_bets[bid] = bet
        for oid in bet.get("outcomes") or []:
            try:
                outcome_ids.add(int(oid))
            except (TypeError, ValueError):
                continue

    filtered_outcomes: dict = {}
    for key, outcome in (state.get("outcomes") or {}).items():
        try:
            oid = int(key)
        except (TypeError, ValueError):
            continue
        if oid in outcome_ids:
            filtered_outcomes[key] = outcome

    filtered_odds: dict = {}
    for key, odd in (state.get("odds") or {}).items():
        try:
            oid = int(key)
        except (TypeError, ValueError):
            continue
        if oid in outcome_ids:
            filtered_odds[key] = odd

    out = dict(state)
    out["matches"] = filtered_matches
    out["bets"] = filtered_bets
    out["outcomes"] = filtered_outcomes
    out["odds"] = filtered_odds
    return out


def _slice_state_for_url(state: dict, url: str) -> dict | None:
    sport_id = _expected_sport_id_for_url(url)
    if sport_id is None:
        return state
    sliced = _filter_winamax_state_by_sport(state, sport_id)
    if sliced is None:
        return None
    n = len(sliced.get("matches") or {})
    print(f"[winamax_dump] Filtre sportId={sport_id} : {n} match(s)")
    return sliced


def _tennis_tournament_urls(state: dict, max_urls: int = 10) -> list[str]:
    sports = state.get("sports") or {}
    tennis = sports.get(str(TENNIS_SPORT_ID)) or sports.get(TENNIS_SPORT_ID) or {}
    cat_ids = tennis.get("categories") or []
    cats = state.get("categories") or {}
    tournaments = state.get("tournaments") or {}
    ranked: list[tuple[int, int]] = []
    for cid in cat_ids:
        cat = cats.get(str(cid)) if isinstance(cats.get(str(cid)), dict) else cats.get(cid)
        if not isinstance(cat, dict):
            continue
        for tid in cat.get("tournaments") or []:
            tour = tournaments.get(str(tid)) if isinstance(tournaments.get(str(tid)), dict) else tournaments.get(tid)
            if not isinstance(tour, dict):
                continue
            try:
                count = int(tour.get("mainMatchCount") or 0)
                tid_int = int(tid)
            except (TypeError, ValueError):
                continue
            if count > 0:
                ranked.append((count, tid_int))
    ranked.sort(key=lambda x: -x[0])
    seen: set[int] = set()
    urls: list[str] = []
    for _, tid in ranked:
        if tid in seen:
            continue
        seen.add(tid)
        urls.append(f"https://www.winamax.fr/paris-sportifs/sports/5/tournaments/{tid}")
        if len(urls) >= max_urls:
            break
    return urls


def _scrape_tennis_tournament_urls(
    page,
    data: dict | None,
    sources: list[str],
) -> dict | None:
    from parser_winamax import winamax_state_missing_tennis

    if not winamax_state_missing_tennis(data or {}):
        return data
    urls = _tennis_tournament_urls(data or {})
    if not urls:
        print("[winamax_dump] Métadonnées tennis sans tournois listés — skip URLs tournois.")
        return data
    print(f"[winamax_dump] Visite {len(urls)} page(s) tournoi tennis…")
    for url in urls:
        if _count_matches_by_sport_id(data or {}).get(TENNIS_SPORT_ID, 0) > 0:
            break
        try:
            chunk, src = _try_url(page, url, state_ready=False)
            if chunk is not None:
                before = _count_matches_by_sport_id(data or {}).get(TENNIS_SPORT_ID, 0)
                data = _merge_winamax_states(data, chunk)
                after = _count_matches_by_sport_id(data or {}).get(TENNIS_SPORT_ID, 0)
                sources.append(f"{url} ({src})")
                print(f"[winamax_dump] Tournoi: tennis {before} -> {after} — {url}")
        except WinamaxDumpError as e:
            print(f"[winamax_dump] tournoi {url} — {e}", file=sys.stderr)
        except Exception as e:
            print(f"[winamax_dump] tournoi ignoré {url}: {e}", file=sys.stderr)
    return data


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


def _merge_winamax_states(base: dict | None, extra: dict) -> dict:
    """Fusionne matches/bets/outcomes/odds (foot + tennis sur une session)."""
    if not base:
        return extra
    out = dict(base)
    for key in ("matches", "bets", "outcomes", "odds", "filters", "categories", "tournaments", "sports"):
        a = out.get(key)
        b = extra.get(key)
        if isinstance(a, dict) and isinstance(b, dict):
            merged = dict(a)
            merged.update(b)
            out[key] = merged
        elif b and not a:
            out[key] = b
    ids = set(out.get("sportIds") or [])
    ids.update(extra.get("sportIds") or [])
    if ids:
        out["sportIds"] = sorted(ids, key=lambda x: int(x) if str(x).isdigit() else x)
    return out


def _count_matches_by_sport_id(data: dict) -> dict[int, int]:
    counts: dict[int, int] = {}
    for match in (data.get("matches") or {}).values():
        if not isinstance(match, dict):
            continue
        sid = match.get("sportId")
        try:
            key = int(sid)
        except (TypeError, ValueError):
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def _expected_sport_id_for_url(url: str) -> int | None:
    m = re.search(r"/sports/(\d+)", url)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _sport_match_count(data: dict, sport_id: int) -> int:
    return sum(
        1
        for match in (data.get("matches") or {}).values()
        if isinstance(match, dict) and match.get("sportId") == sport_id
    )


def _pick_best_state(candidates: list[dict], expected_sport: int | None) -> dict:
    if not candidates:
        raise ValueError("candidates vide")
    if expected_sport is None:
        return candidates[-1]
    best = candidates[-1]
    best_n = _sport_match_count(best, expected_sport)
    for chunk in candidates:
        n = _sport_match_count(chunk, expected_sport)
        if n > best_n:
            best = chunk
            best_n = n
    return best


def _write_dump_and_log(data: dict, source: str) -> None:
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    n_matches = len(data.get("matches", {}))
    by_sport = _count_matches_by_sport_id(data)
    n_foot = by_sport.get(1, 0)
    n_tennis = by_sport.get(TENNIS_SPORT_ID, 0)
    print("[winamax_dump] SUCCES")
    print(f"  Source  : {source}")
    print(f"  Fichier : {OUT}")
    print(f"  Matchs  : {n_matches} (foot={n_foot}, tennis={n_tennis})")
    if n_tennis == 0:
        print(
            "[winamax_dump] ATTENTION: 0 match tennis (sportId=5) — "
            "vérifier scrape sports/5 et VELORA_PROXY_URL.",
            file=sys.stderr,
        )


def _try_url(page, url: str, *, state_ready: bool) -> tuple[object | None, str]:
    print(f"[winamax_dump] Navigation -> {url}")
    expected_sport = _expected_sport_id_for_url(url)
    captured: list[dict] = []

    def on_response(response) -> None:
        try:
            if response.status != 200:
                return
            url = response.url.lower()
            if "winamax" not in url:
                return
            is_socket = "uof-sports-server" in url or "socket.io" in url
            ctype = (response.headers.get("content-type") or "").lower()
            if is_socket or "json" in ctype or "text/plain" in ctype or "javascript" in ctype:
                try:
                    body = response.text()
                except Exception:
                    return
                for chunk in _harvest_state_chunks_from_text(body):
                    captured.append(chunk)
                if "json" in ctype and not is_socket:
                    try:
                        norm, _ = _normalize_state(response.json())
                        if norm is not None:
                            captured.append(norm)
                    except Exception:
                        pass
        except Exception:
            pass

    page.on("response", on_response)

    try:
        wait_until = "networkidle" if expected_sport == TENNIS_SPORT_ID else "domcontentloaded"
        try:
            page.goto(url, timeout=GOTO_TIMEOUT_MS, wait_until=wait_until)
        except Exception as e:
            print(f"[winamax_dump] ATTENTION chargement ({wait_until}): {e}")
            page.goto(url, timeout=GOTO_TIMEOUT_MS, wait_until="domcontentloaded")

        _dismiss_cookie_banner(page)

        if expected_sport == TENNIS_SPORT_ID:
            _scroll_to_load_matches(page)
            _click_tennis_nav(page)
            state_ready = _wait_for_sport_id_matches(page, TENNIS_SPORT_ID)
            polled = _poll_tennis_matches_in_page(page)
            if polled > 0:
                state_ready = True
        elif "/tournaments/" in url and "/sports/5" in url:
            _scroll_to_load_matches(page)
            polled = _poll_tennis_matches_in_page(page, max_seconds=min(TENNIS_POLL_SEC, 25.0))
            if polled > 0:
                state_ready = True
        elif not state_ready:
            state_ready = _wait_for_sport_state(page)

        if not state_ready and _strict_on_wait_timeout():
            raise WinamaxDumpError(
                f"PRELOADED_STATE indisponible après {WAIT_STATE_MS}ms sur {url}. "
                "Winamax n'a pas exposé les matchs (géoblocage, cookies, ou page vide). "
                "Essayez VELORA_PROXY_URL ou relancez plus tard."
            )

        time.sleep(2.5 if expected_sport == TENNIS_SPORT_ID else 1.0)

        if captured:
            best = _pick_best_state(captured, expected_sport)
            sliced = _slice_state_for_url(best, url)
            if sliced is not None:
                n = len(sliced.get("matches") or {})
                n_target = _sport_match_count(sliced, expected_sport) if expected_sport else n
                return sliced, f"réponse réseau ({n} matchs, sport cible={n_target})"

        data, source = _extract_via_evaluate(page)
        if data is not None:
            norm, label = _normalize_state(data)
            if norm is not None:
                sliced = _slice_state_for_url(norm, url)
                if sliced is not None:
                    return sliced, f"{source} {label}"

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
        norm, label = _normalize_state(data)
        if norm is not None:
            sliced = _slice_state_for_url(norm, url)
            if sliced is not None:
                return sliced, f"{source} {label}"
        return data, source
    finally:
        page.remove_listener("response", on_response)


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
    if OUT.is_file() and OUT.stat().st_size > 10:
        print(
            f"[winamax_dump] Conservation du dump existant ({OUT.name}) — "
            "échec scrape sans écraser les données.",
            file=sys.stderr,
        )
    else:
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

    merged: dict | None = None
    sources: list[str] = []
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
            sliced = _slice_state_for_url(norm, url)
            if sliced is None:
                continue
            merged = _merge_winamax_states(merged, sliced)
            n_tennis = _sport_match_count(merged, TENNIS_SPORT_ID)
            sources.append(f"{url} ({source} {label}, tennis={n_tennis})")
    if merged is not None:
        return merged, "HTTP merge: " + "; ".join(sources)
    return None, ""


def _log_proxy_ci() -> None:
    """Diagnostic secret proxy (host/port uniquement, jamais les identifiants)."""
    if os.environ.get("GITHUB_ACTIONS", "").strip().lower() != "true":
        return
    proxy_url = os.environ.get("VELORA_PROXY_URL", "").strip()
    if not proxy_url:
        print(
            "[winamax_dump] CI sans VELORA_PROXY_URL : risque de géoblocage Winamax "
            "(runner hors France). Ajoutez un proxy FR en secret GitHub.",
            file=sys.stderr,
        )
        return
    from urllib.parse import urlparse

    parsed = urlparse(proxy_url)
    host = parsed.hostname or "(host invalide)"
    port = parsed.port or "défaut 6645"
    auth = "oui" if parsed.username or os.environ.get("VELORA_PROXY_USER") else "non"
    print(f"[winamax_dump] CI proxy configuré — host={host} port={port} auth={auth}")


def main() -> None:
    _log_proxy_ci()

    data, source = _try_http_regex()
    if data is not None:
        norm, _ = _normalize_state(data)
        if norm is None:
            _fail("JSON HTTP trouvé mais sans matches/outcomes valides")
        _write_dump_and_log(norm, source)
        return

    print("[winamax_dump] Demarrage Chromium (SSR)...")
    proxy_url = os.environ.get("VELORA_PROXY_URL", "").strip()
    interactive_proxy = proxy_interactive_enabled() and bool(proxy_url)
    headless = False if interactive_proxy else _chromium_headless()
    proxy_cfg = resolve_playwright_proxy_config()
    context_kwargs: dict[str, Any] = {
        "user_agent": UA,
        "locale": "fr-FR",
        "timezone_id": "Europe/Paris",
        "viewport": {"width": 1920, "height": 1080},
        "extra_http_headers": {"Accept-Language": "fr-FR,fr;q=0.9"},
    }
    if proxy_cfg:
        context_kwargs["proxy"] = proxy_cfg
        mode = "interactif (login dans Chromium)" if interactive_proxy else "automatique"
        print(f"[winamax_dump] Proxy ({mode}): {proxy_cfg.get('server')}")

    launch_kwargs: dict[str, Any] = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    }

    browser = None
    context = None
    data = None
    source = ""

    try:
        with sync_playwright() as p:
            print(f"[winamax_dump] chromium headless={headless}")
            if interactive_proxy:
                profile = proxy_user_data_dir()
                profile.mkdir(parents=True, exist_ok=True)
                context = p.chromium.launch_persistent_context(
                    str(profile),
                    **launch_kwargs,
                    **context_kwargs,
                )
                page = context.pages[0] if context.pages else context.new_page()
            else:
                browser = p.chromium.launch(**launch_kwargs)
                context = browser.new_context(**context_kwargs)
                page = context.new_page()
            page.set_default_timeout(min(EVALUATE_TIMEOUT_MS, 60_000))

            if interactive_proxy:
                wait_for_proxy_authentication(page, URLS[0], label="winamax_dump")

            last_err: WinamaxDumpError | None = None
            sources: list[str] = []
            for url in URLS:
                try:
                    chunk, src = _try_url(page, url, state_ready=False)
                    if chunk is not None:
                        data = _merge_winamax_states(data, chunk)
                        sources.append(f"{url} ({src})")
                        by_sport = _count_matches_by_sport_id(data)
                        print(
                            f"[winamax_dump] OK via {src} — {url} "
                            f"(merge foot={by_sport.get(1, 0)}, tennis={by_sport.get(TENNIS_SPORT_ID, 0)})"
                        )
                except WinamaxDumpError as e:
                    last_err = e
                    print(f"[winamax_dump] {url} — {e}", file=sys.stderr)
                except Exception as e:
                    print(f"[winamax_dump] URL {url} ignorée: {e}", file=sys.stderr)

            tennis_retries = int(os.environ.get("VELORA_DUMP_TENNIS_RETRIES", "2"))
            for attempt in range(1, tennis_retries + 1):
                if data is None:
                    break
                if _count_matches_by_sport_id(data).get(TENNIS_SPORT_ID, 0) > 0:
                    break
                print(
                    f"[winamax_dump] Tennis absent — retry {attempt}/{tennis_retries} sur {TENNIS_URL}",
                    file=sys.stderr,
                )
                try:
                    chunk, src = _try_url(page, TENNIS_URL, state_ready=False)
                    if chunk is not None:
                        before = _count_matches_by_sport_id(data).get(TENNIS_SPORT_ID, 0)
                        data = _merge_winamax_states(data, chunk)
                        after = _count_matches_by_sport_id(data).get(TENNIS_SPORT_ID, 0)
                        sources.append(f"tennis-retry-{attempt} ({src})")
                        print(f"[winamax_dump] Retry tennis: {before} -> {after} match(s)")
                except WinamaxDumpError as e:
                    print(f"[winamax_dump] retry tennis — {e}", file=sys.stderr)
                except Exception as e:
                    print(f"[winamax_dump] retry tennis ignoré: {e}", file=sys.stderr)

            data = _scrape_tennis_tournament_urls(page, data, sources)

            if data is not None and sources:
                source = " + ".join(sources)
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

    _write_dump_and_log(data, source)


if __name__ == "__main__":
    try:
        main()
    except WinamaxDumpError as e:
        print(f"[winamax_dump] {e}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
