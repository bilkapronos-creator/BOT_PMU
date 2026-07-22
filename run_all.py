"""
Pipeline Velora Engine — point d'entrée unique (planificateur Windows + GitHub Actions).

  python run_all.py

Orchestration :
  1–3  Scraper Winamax (dump → parser → sniper) → api_velora_premium.json
  4    Copie JSON vers le projet web (web/ ou VELORA_WEB_DIR)
  5a   Résolution archives PMU (API PMU ordreArrivee + rapports) → Supabase/SQLite
  5b   Résolution Foot (scores Winamax + validation) → velora_archives_foot.json
  5c   Publication vitrine → api_velora_communaute.json
  6    git add / commit / push (sauf VELORA_SKIP_GIT_PUSH=1) → Vercel

Planificateur Windows : programmer uniquement ce script (pas resolver_pmu à part).
CI : .github/workflows/velora_cron.yml (toutes les 30 min).

Surcharge : VELORA_WEB_DIR=C:\\chemin\\vers\\web
Désactiver le push : VELORA_SKIP_GIT_PUSH=1
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ERROR_LOG = ROOT / "error_log.txt"
DUMP_HTML = ROOT / "dump_winamax_html.json"
MATCHS_JSON = ROOT / "api_velora_matchs.json"
PREMIUM_SRC = ROOT / "api_velora_premium.json"
GIT_COMMIT_MSG = "Mise à jour automatique Velora Data"
GIT_JSON_FILES = (
    "api_velora_matchs.json",
    "api_velora_matchs_tennis.json",
    "api_velora_premium.json",
    "api_velora_communaute.json",
    "velora_foot_resultats.json",
    "velora_archives_foot.json",
    "velora_odds_history.json",
    "velora_foot_calibration.json",
    "velora_pronos_history.json",
)

PYTHON = Path(sys.executable).resolve()
SNIPER_LIMIT = os.environ.get("SNIPER_LIMIT", "25")
INTERNET_WAIT_SECONDS = 300
INTERNET_CHECK_TIMEOUT = 8

WEB_ROOT: Path | None = None
MATCHS_DEPLOY: Path | None = None
PREMIUM_DEPLOY: Path | None = None
COMMUNAUTE_DEPLOY: Path | None = None


def log(msg: str) -> None:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(msg, flush=True)


def log_error(step: str, message: str, details: str = "") -> None:
    """Affiche l'erreur sur stdout (logs CI) et la copie dans error_log.txt."""
    sep = "=" * 60
    block_lines = [sep, f"ERREUR — {step}", sep, message]
    if details:
        block_lines.extend(["", details.rstrip()])
    block_lines.append(sep)
    for line in block_lines:
        log(line)
    try:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with ERROR_LOG.open("a", encoding="utf-8") as f:
            f.write(f"\n[{stamp}] {step}\n{message}\n")
            if details:
                f.write(f"{details.rstrip()}\n")
            f.write("-" * 60 + "\n")
    except Exception:
        pass
    log(f"(copie également dans {ERROR_LOG})")


def log_success(step: str, message: str) -> None:
    log(message)
    try:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with ERROR_LOG.open("a", encoding="utf-8") as f:
            f.write(f"\n[{stamp}] {step}\n{message}\n")
    except Exception:
        pass


def resolve_web_project_dir() -> Path:
    candidates: list[Path] = []
    env_dir = os.environ.get("VELORA_WEB_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    # Monorepo : web/ à la racine du dépôt scraper
    candidates.append(ROOT / "web")
    candidates.append(ROOT)
    candidates.append(ROOT.parent / "BOT_PMU")

    tried: list[str] = []
    for raw in candidates:
        folder = raw.expanduser().resolve()
        index = folder / "index.html"
        tried.append(f"{folder}  (index.html: {'oui' if index.is_file() else 'non'})")
        if index.is_file():
            return folder

    msg = (
        "Projet web Velora introuvable — index.html absent.\n"
        "Chemins testés :\n  " + "\n  ".join(tried) + "\n"
        f"Scraper (ROOT) : {ROOT}\n"
        "Définissez VELORA_WEB_DIR vers le dossier qui contient index.html."
    )
    raise FileNotFoundError(msg)


def _charger_variables_env_fichier(path: Path) -> None:
    """Charge un .env local (gitignore) sans écraser les variables déjà définies."""
    if not path.is_file():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass


def init_deploy_paths() -> None:
    global WEB_ROOT, MATCHS_DEPLOY, PREMIUM_DEPLOY, COMMUNAUTE_DEPLOY
    WEB_ROOT = resolve_web_project_dir()
    for env_path in (ROOT / ".env", WEB_ROOT / ".env"):
        _charger_variables_env_fichier(env_path)
    MATCHS_DEPLOY = (WEB_ROOT / "api_velora_matchs.json").resolve()
    PREMIUM_DEPLOY = (WEB_ROOT / "api_velora_premium.json").resolve()
    COMMUNAUTE_DEPLOY = (WEB_ROOT / "api_velora_communaute.json").resolve()


def _fmt_file_info(path: Path) -> str:
    if not path.is_file():
        return f"{path} — ABSENT"
    st = path.stat()
    when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return f"{path} ({st.st_size:,} octets, modifié {when})"


def _verify_copy(src: Path, dest: Path) -> None:
    if not dest.is_file():
        raise OSError(f"Copie échouée : {dest} introuvable après copy2")
    if dest.stat().st_size != src.stat().st_size:
        raise OSError(
            f"Taille incohérente après copie : src={src.stat().st_size} dest={dest.stat().st_size}"
        )


def print_deploy_summary() -> None:
    assert MATCHS_DEPLOY and PREMIUM_DEPLOY and COMMUNAUTE_DEPLOY and WEB_ROOT
    log("")
    log("=" * 60)
    log("DÉPLOIEMENT LOCAL — chemins absolus des fichiers déposés")
    log("=" * 60)
    log(f"Projet web (index.html) : {WEB_ROOT}")
    log(f"  -> {_fmt_file_info(MATCHS_DEPLOY)}")
    log(f"  -> {_fmt_file_info(PREMIUM_DEPLOY)}")
    log(f"  -> {_fmt_file_info(COMMUNAUTE_DEPLOY)}")
    log("=" * 60)
    log("")


def internet_available() -> bool:
    targets = [
        ("www.winamax.fr", 443),
        ("1.1.1.1", 53),
        ("8.8.8.8", 53),
    ]
    for host, port in targets:
        try:
            with socket.create_connection((host, port), timeout=INTERNET_CHECK_TIMEOUT):
                return True
        except OSError:
            continue
    return False


def wait_for_internet() -> None:
    while not internet_available():
        log(
            "Pas de connexion internet détectée. "
            f"Nouvelle tentative dans {INTERNET_WAIT_SECONDS // 60} minutes…"
        )
        time.sleep(INTERNET_WAIT_SECONDS)
    log("Connexion internet OK.")


def _env_sous_processus(extra: dict | None = None) -> dict[str, str]:
    """Copie l'environnement ; sur GitHub Actions force Chromium classique (pas headless-shell)."""
    env = {k: str(v) for k, v in os.environ.items()}
    env["VELORA_ROOT"] = str(ROOT)
    env["VELORA_SCRAPER_DIR"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    if os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true":
        env["VELORA_HEADLESS"] = "0"
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


def run_step(script: str, label: str, extra_env: dict | None = None) -> bool:
    path = (ROOT / script).resolve()
    if not path.is_file():
        log_error(label, f"Script introuvable : {path}")
        return False

    env = _env_sous_processus(extra_env)

    log(f"--- {label} ---")
    log(f"Commande : {PYTHON} {path.name}")
    try:
        result = subprocess.run(
            [str(PYTHON), "-u", str(path)],
            cwd=str(ROOT),
            env=env,
        )
    except Exception as e:
        log_error(label, str(e), traceback.format_exc())
        return False

    if result.returncode != 0:
        log_error(
            label,
            f"Code de sortie {result.returncode} pour {path.name}",
            "Relisez la sortie du script ci-dessus (traceback / messages Playwright).",
        )
        return False
    return True


def _git_cwd() -> Path:
    assert WEB_ROOT is not None
    return ROOT if (ROOT / ".git").is_dir() else WEB_ROOT


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(_git_cwd()),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _files_for_git_commit() -> list[str]:
    """Chemins relatifs au dépôt Git (racine + web/ en monorepo)."""
    git_root = _git_cwd().resolve()
    out: list[str] = []
    seen: set[str] = set()
    bases: list[Path] = [ROOT]
    if WEB_ROOT is not None:
        bases.append(WEB_ROOT)
    for base in bases:
        for name in GIT_JSON_FILES:
            path = (base / name).resolve()
            if not path.is_file():
                continue
            try:
                rel = path.relative_to(git_root)
            except ValueError:
                continue
            key = rel.as_posix()
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def _commit_sans_modifications(result: subprocess.CompletedProcess[str]) -> bool:
    blob = f"{result.stdout or ''}{result.stderr or ''}".lower()
    indicateurs = (
        "nothing to commit",
        "no changes added to commit",
        "rien à valider",
        "aucune modification",
    )
    return any(x in blob for x in indicateurs)


def push_vercel_git() -> bool:
    """Étape 5 : git add / commit / push dans BOT_PMU pour déclencher Vercel."""
    assert WEB_ROOT is not None
    label = "Étape 5/5 : push Git vers Vercel"

    if os.environ.get("VELORA_SKIP_GIT_PUSH", "").strip() in ("1", "true", "yes"):
        log(f"{label} — ignorée (VELORA_SKIP_GIT_PUSH).")
        return True

    git_root = ROOT if (ROOT / ".git").is_dir() else WEB_ROOT
    git_dir = git_root / ".git"
    if not git_dir.is_dir():
        log_error(label, f"Dépôt Git introuvable (testé {ROOT} et {WEB_ROOT})")
        return False

    log(f"--- {label} ---")
    log(f"Dépôt Git : {git_root.resolve()}")

    try:
        to_commit = _files_for_git_commit()
        if not to_commit:
            log("Aucun fichier JSON à committer")
            log_success(label, "Aucun JSON présent — pipeline OK sans push")
            return True

        r_fetch = run_git(["git", "fetch", "origin", "main"])
        if r_fetch.returncode != 0:
            log_error(label, "git fetch origin main a échoué", f"{r_fetch.stdout}\n{r_fetch.stderr}")
            return False

        r_merge = run_git(["git", "merge", "--ff-only", "origin/main"])
        if r_merge.returncode != 0:
            log_error(
                label,
                "git merge --ff-only origin/main a échoué",
                f"{r_merge.stdout}\n{r_merge.stderr}",
            )
            return False

        r_add = run_git(["git", "add", "-f", *to_commit])
        if r_add.returncode != 0:
            log_error(label, "git add -f a échoué", f"{r_add.stdout}\n{r_add.stderr}")
            return False
        log(f"  git add -f {' '.join(to_commit)}")

        r_commit = run_git(["git", "commit", "-m", GIT_COMMIT_MSG])
        if r_commit.returncode != 0:
            if _commit_sans_modifications(r_commit):
                log("Aucune modification à pusher")
                log_success(label, "Aucune modification à pusher — pipeline OK sans push")
                return True
            log_error(label, "git commit a échoué", f"{r_commit.stdout}\n{r_commit.stderr}")
            return False
        log(f'  git commit -m "{GIT_COMMIT_MSG}"')

        r_push = run_git(["git", "push", "origin", "HEAD:main"])
        if r_push.returncode != 0:
            log_error(label, "git push a échoué", f"{r_push.stdout}\n{r_push.stderr}")
            return False
        if r_push.stdout.strip():
            log(r_push.stdout.strip())
        if r_push.stderr.strip():
            log(r_push.stderr.strip())

        msg = (
            f"Push Git réussi depuis {_git_cwd().resolve()} "
            f"({', '.join(to_commit)})"
        )
        log_success(label, msg)
        log(msg)
        return True

    except Exception as e:
        log_error(label, str(e), traceback.format_exc())
        return False


def deploy_json() -> bool:
    assert MATCHS_DEPLOY and PREMIUM_DEPLOY and COMMUNAUTE_DEPLOY and WEB_ROOT
    label = "Étape 4/5 : déploiement local vers projet web"
    try:
        for src in (MATCHS_JSON, PREMIUM_SRC):
            if not src.is_file():
                raise FileNotFoundError(f"Fichier absent : {src.resolve()}")

        log(f"Source matchs  : {_fmt_file_info(MATCHS_JSON)}")
        log(f"Source premium : {_fmt_file_info(PREMIUM_SRC)}")

        if MATCHS_JSON.resolve() != MATCHS_DEPLOY:
            shutil.copy2(MATCHS_JSON.resolve(), MATCHS_DEPLOY)
            _verify_copy(MATCHS_JSON, MATCHS_DEPLOY)
        else:
            log("  matchs : même chemin (monorepo), copie ignorée")

        if PREMIUM_SRC.resolve() != PREMIUM_DEPLOY:
            shutil.copy2(PREMIUM_SRC.resolve(), PREMIUM_DEPLOY)
            _verify_copy(PREMIUM_SRC, PREMIUM_DEPLOY)
        else:
            log("  premium : même chemin (monorepo), copie ignorée")

        communaute = (WEB_ROOT / "api_velora_communaute.json").resolve()
        if communaute.is_file() and communaute.resolve() != COMMUNAUTE_DEPLOY:
            shutil.copy2(communaute, COMMUNAUTE_DEPLOY)
            _verify_copy(communaute, COMMUNAUTE_DEPLOY)

        sync_script = (ROOT / "scripts" / "sync_web_data.py").resolve()
        if sync_script.is_file():
            log("  sync web : sanitize scores + historique cotes + calibration")
            sync_ok = subprocess.run(
                [str(PYTHON), "-u", str(sync_script)],
                cwd=str(ROOT),
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
            ).returncode == 0
            if not sync_ok:
                log("  sync web : échec (copie brute conservée)")
        else:
            try:
                from velora_engine.odds_snapshots import snapshot_from_json_file

                hist = (WEB_ROOT / "velora_odds_history.json").resolve()
                snapshot_from_json_file(MATCHS_DEPLOY, hist)
                log(f"  historique cotes : {_fmt_file_info(hist)}")
            except Exception as snap_err:
                log(f"  historique cotes : ignoré ({snap_err})")

        log_success(label, "Déploiement des JSON vers BOT_PMU réussi")
        print_deploy_summary()
        return True
    except Exception as e:
        log_error(label, str(e), traceback.format_exc())
        return False


def run_web_script(script: str, label: str) -> bool:
    """Exécute un script Python dans le projet web (BOT_PMU)."""
    assert WEB_ROOT is not None
    path = (WEB_ROOT / script).resolve()
    if not path.is_file():
        log(f"[web] Script absent, ignoré : {path.name}")
        return True

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["VELORA_SCRAPER_DIR"] = str(ROOT)
    env["PYTHONUNBUFFERED"] = "1"
    log(f"--- {label} ---")
    log(f"Commande : {PYTHON} {path.name}")
    try:
        result = subprocess.run(
            [str(PYTHON), "-u", str(path)],
            cwd=str(WEB_ROOT),
            env=env,
        )
    except Exception as e:
        log_error(label, str(e), traceback.format_exc())
        return False

    if result.returncode != 0:
        log_error(
            label,
            f"Code de sortie {result.returncode} pour {path.name}",
            "Relisez la sortie du script ci-dessus.",
        )
        return False
    return True


def _ci_scraper_optionnel() -> bool:
    return os.environ.get("VELORA_CI_SCRAPER_OPTIONAL", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _fichier_json_recent(path: Path, max_heures: float) -> bool:
    if not path.is_file() or path.stat().st_size < 80:
        return False
    age_h = (time.time() - path.stat().st_mtime) / 3600.0
    return age_h <= max_heures


def _match_kickoff_paris(match: dict):
    """datetime Europe/Paris du coup d'envoi, ou None."""
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    ts = _match_start_ts_unix(match)
    if ts is None:
        return None
    return _dt.fromtimestamp(ts, tz=ZoneInfo("Europe/Paris"))


def _match_start_ts_unix(match: dict) -> float | None:
    raw = match.get("match_start_ts")
    if raw is not None:
        try:
            ts = float(raw)
            if ts > 1e12:
                ts /= 1000.0
            if ts > 0:
                return ts
        except (TypeError, ValueError):
            pass
    dm = str(match.get("date_match") or "").strip()
    parts = dm.split(" à ")
    if len(parts) != 2:
        return None
    try:
        d, mo, y = parts[0].split("/")
        h, mi = parts[1].split(":")
        from datetime import datetime as _dt

        return _dt(int(y), int(mo), int(d), int(h), int(mi)).timestamp()
    except (ValueError, TypeError):
        return None


MATCH_ENDED_STATUSES = frozenset(
    {"ENDED", "CLOSED", "FINISHED", "CANCELLED", "CANCELED"}
)
MATCH_FINISHED_GRACE_SEC = 120 * 60


def _json_contient_match_journee_pari(path: Path) -> bool:
    """Vrai si au moins un match est dans la fenêtre betting_day (alignée parser Winamax)."""
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    matchs = data if isinstance(data, list) else data.get("matchs") or []
    if not matchs:
        return False
    from parser_winamax import is_kickoff_in_betting_day

    for m in matchs:
        if not isinstance(m, dict):
            continue
        kickoff = _match_kickoff_paris(m)
        if kickoff is not None and is_kickoff_in_betting_day(kickoff):
            return True
    return False


def _match_est_termine_ou_live(match: dict) -> bool:
    status = str(match.get("match_status") or "").strip().upper().replace(" ", "_")
    if status in MATCH_ENDED_STATUSES:
        return True
    if status in {"LIVE", "RUNNING", "INPLAY", "IN_PLAY"}:
        return False
    ts = _match_start_ts_unix(match)
    if ts is None:
        return False
    return ts < time.time() - MATCH_FINISHED_GRACE_SEC


def _json_contient_matchs_a_venir_journee(path: Path) -> bool:
    """Matchs betting_day encore à jouer (ou en direct) — sinon re-scrape requis."""
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    matchs = data if isinstance(data, list) else data.get("matchs") or []
    if not matchs:
        return False
    from parser_winamax import is_kickoff_in_betting_day

    for m in matchs:
        if not isinstance(m, dict):
            continue
        kickoff = _match_kickoff_paris(m)
        if kickoff is None or not is_kickoff_in_betting_day(kickoff):
            continue
        if not _match_est_termine_ou_live(m):
            return True
    return False


def _json_meta_age_hours(path: Path) -> float | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        raw = (data.get("meta") or {}).get("generated_at")
        if not raw:
            return None
        from datetime import datetime as _dt

        dt = _dt.fromisoformat(str(raw).replace("Z", "+00:00"))
        return (time.time() - dt.timestamp()) / 3600.0
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _json_effective_age_hours(path: Path) -> float:
    file_age = (time.time() - path.stat().st_mtime) / 3600.0
    meta_age = _json_meta_age_hours(path)
    if meta_age is not None:
        return max(file_age, meta_age)
    return file_age


def _json_skip_scraper_ci(path: Path, max_heures: float) -> bool:
    """Skip proactif CI : fichier récent, meta fraîche ET matchs du jour encore à jouer."""
    if not path.is_file() or path.stat().st_size < 4:
        return False
    if _json_effective_age_hours(path) > max_heures:
        return False
    return _json_contient_matchs_a_venir_journee(path)


def _json_utilisable_ci(path: Path, max_heures: float) -> bool:
    """
    Repli CI : fichier récent ET au moins un match à venir / live (journée de paris).
    Évite de conserver un premium « frais » alors que tous les matchs sont terminés.
    """
    if not path.is_file() or path.stat().st_size < 4:
        return False
    age_h = (time.time() - path.stat().st_mtime) / 3600.0
    if age_h > max_heures:
        return False
    return _json_contient_matchs_a_venir_journee(path)


def _json_matchs_a_venir_count(path: Path) -> int:
    """Nombre de matchs non terminés dans api_velora_matchs.json."""
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    matchs = data if isinstance(data, list) else data.get("matchs") or []
    return sum(
        1
        for m in matchs
        if isinstance(m, dict) and not _match_est_termine_ou_live(m)
    )


def _matchs_json_necessite_parser(path: Path) -> bool:
    """True si le parser doit regénérer ce JSON (0 à venir, meta vieille, dump plus récent)."""
    if not path.is_file() or path.stat().st_size < 4:
        return True
    if _json_matchs_a_venir_count(path) == 0:
        return True
    max_age = float(os.environ.get("VELORA_CI_MATCHS_MAX_AGE_H", "6"))
    meta_age = _json_meta_age_hours(path)
    if meta_age is not None and meta_age > max_age:
        return True
    if DUMP_HTML.is_file() and path.stat().st_mtime + 30 < DUMP_HTML.stat().st_mtime:
        return True
    return False


def _matchs_json_besoin_regeneration() -> bool:
    assert WEB_ROOT is not None
    paths: list[Path] = []
    seen: set[str] = set()
    for p in (MATCHS_JSON, WEB_ROOT / "api_velora_matchs.json"):
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            paths.append(p)
    return any(_matchs_json_necessite_parser(p) for p in paths)


def _aucun_match_foot_a_venir_ci() -> bool:
    """Vrai si aucun match à venir dans les JSON Foot — scrape obligatoire."""
    assert WEB_ROOT is not None
    for path in (MATCHS_JSON, WEB_ROOT / "api_velora_matchs.json"):
        if path.is_file() and _json_matchs_a_venir_count(path) > 0:
            return False
    return True


def _dump_utilisable_pour_parser(path: Path) -> bool:
    """Dump présent ET contient au moins un match football non terminé."""
    return path.is_file() and path.stat().st_size > 80 and _dump_contient_matchs_a_venir(path)


def _dump_contient_match_journee_pari(path: Path) -> bool:
    """Vrai si le dump Winamax contient au moins un match dans la fenêtre betting_day."""
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    matches = data.get("matches") or {}
    if not isinstance(matches, dict):
        return False
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    from parser_winamax import format_match_start, is_kickoff_in_betting_day

    tz = ZoneInfo("Europe/Paris")
    for m in matches.values():
        if not isinstance(m, dict):
            continue
        ts, _ = format_match_start(m.get("matchStart") or m.get("matchStartDate"))
        if ts is None:
            continue
        kickoff = _dt.fromtimestamp(float(ts), tz=tz)
        if is_kickoff_in_betting_day(kickoff):
            return True
    return False


def _dump_contient_matchs_a_venir(path: Path) -> bool:
    """Vrai si le dump contient au moins un match football non terminé."""
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    matches = data.get("matches") or {}
    if not isinstance(matches, dict):
        return False
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    from parser_winamax import format_match_start, is_winamax_match_finished

    tz = ZoneInfo("Europe/Paris")
    now = _dt.now(tz=tz)
    for m in matches.values():
        if not isinstance(m, dict) or m.get("sportId") != 1:
            continue
        ts, _ = format_match_start(m.get("matchStart") or m.get("matchStartDate"))
        if ts is None:
            continue
        if not is_winamax_match_finished(m, ts, now):
            return True
    return False


def _dump_skip_scraper_ci(path: Path, max_heures: float) -> bool:
    """Skip proactif CI : dump récent ET matchs football à venir."""
    if not path.is_file() or path.stat().st_size < 80:
        return False
    age_h = (time.time() - path.stat().st_mtime) / 3600.0
    if age_h > max_heures:
        return False
    if not _dump_contient_matchs_a_venir(path):
        return False
    try:
        from parser_winamax import winamax_state_missing_tennis

        data = json.loads(path.read_text(encoding="utf-8"))
        if winamax_state_missing_tennis(data):
            return False
    except (OSError, json.JSONDecodeError):
        pass
    return True


def _dump_utilisable_ci(path: Path, max_heures: float) -> bool:
    """Repli CI : dump récent avec matchs betting_day."""
    return _dump_skip_scraper_ci(path, max_heures)


def _synchroniser_premium_depuis_web() -> bool:
    """Copie web/api_velora_premium.json → racine si le dépôt n'a pas été régénéré."""
    assert WEB_ROOT is not None
    src = (WEB_ROOT / "api_velora_premium.json").resolve()
    if not src.is_file():
        return False
    if PREMIUM_SRC.resolve() != src:
        shutil.copy2(src, PREMIUM_SRC)
    if MATCHS_JSON.resolve() != (WEB_ROOT / "api_velora_matchs.json").resolve():
        matchs_web = WEB_ROOT / "api_velora_matchs.json"
        if matchs_web.is_file():
            shutil.copy2(matchs_web, MATCHS_JSON)
    return PREMIUM_SRC.is_file()


def _mode_repli_scraper_ci(*, proactif: bool = False) -> str | None:
    """premium | dump | matchs | None — données déjà présentes dans le dépôt."""
    max_h = float(os.environ.get("VELORA_CI_SCRAPER_MAX_AGE_H", "6"))
    frais = _json_skip_scraper_ci if proactif else _json_utilisable_ci
    dump_frais = _dump_skip_scraper_ci if proactif else _dump_utilisable_ci
    if frais(PREMIUM_SRC, max_h):
        return "premium"
    assert WEB_ROOT is not None
    premium_web = WEB_ROOT / "api_velora_premium.json"
    if frais(premium_web, max_h):
        return "premium_web"
    if dump_frais(DUMP_HTML, max_h):
        return "dump"
    if frais(MATCHS_JSON, max_h):
        return "matchs"
    matchs_web = WEB_ROOT / "api_velora_matchs.json"
    if frais(matchs_web, max_h):
        return "matchs_web"
    return None


def _log_etat_matchs_json(path: Path) -> None:
    """Diagnostic : journée de paris vs matchs périmés (recherche Foot vide côté site)."""
    from parser_winamax import is_kickoff_in_betting_day

    if not path.is_file():
        log(f"[matchs] {path.name} absent.")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"[matchs] {path.name} illisible : {exc}")
        return
    matchs = data if isinstance(data, list) else data.get("matchs") or []
    jour = sum(
        1
        for m in matchs
        if isinstance(m, dict)
        and _match_kickoff_paris(m) is not None
        and is_kickoff_in_betting_day(_match_kickoff_paris(m))
    )
    futurs = sum(
        1
        for m in matchs
        if isinstance(m, dict)
        and _match_start_ts_unix(m) is not None
        and _match_start_ts_unix(m) > time.time()
    )
    log(
        f"[matchs] {path.name} : {len(matchs)} entrée(s), "
        f"{jour} dans la journée de paris, {futurs} à venir."
    )
    if matchs and jour == 0:
        log(
            "[matchs] Aucun match du jour — le moteur de recherche Foot sera vide "
            "jusqu'à un scrape Winamax réussi (proxy France requis sur GitHub Actions)."
        )


def _log_etat_dump_winamax(path: Path) -> None:
    """Diagnostic dump Winamax : réutilisation CI vs scrape frais."""
    if not path.is_file():
        log(f"[dump] {path.name} absent.")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"[dump] {path.name} illisible : {exc}")
        return
    matches = data.get("matches") or {}
    total = len(matches) if isinstance(matches, dict) else 0
    jour = _dump_contient_match_journee_pari(path)
    age_h = (time.time() - path.stat().st_mtime) / 3600.0
    log(
        f"[dump] {path.name} : {total} match(s) Winamax, "
        f"journée de paris={'oui' if jour else 'non'}, âge {age_h:.1f} h."
    )
    if total and not jour:
        log(
            "[dump] Dump périmé (veille) — le CI ne doit pas s'en contenter "
            "sans nouveau scrape Winamax."
        )


def executer_phase_scraper() -> bool:
    """Étapes 1–3 : dump → parser → sniper (repli CI si géoblocage Winamax)."""
    _log_etat_dump_winamax(DUMP_HTML)
    _log_etat_matchs_json(MATCHS_JSON)
    assert WEB_ROOT is not None
    _log_etat_matchs_json(WEB_ROOT / "api_velora_matchs.json")

    etapes = [
        ("winamax_dump.py", "Étape 1/5 : extraction SSR Winamax", None),
        ("parser_winamax.py", "Étape 2/5 : structuration JSON Foot", None),
        ("parser_winamax_tennis.py", "Étape 2b/5 : structuration JSON Tennis", None),
        (
            "winamax_sniper.py",
            f"Étape 3/5 : enrichissement sniper (max {SNIPER_LIMIT})",
            {"SNIPER_LIMIT": SNIPER_LIMIT},
        ),
    ]

    debut = 0
    if _ci_scraper_optionnel():
        mode = _mode_repli_scraper_ci(proactif=True)
        tennis_json_vide = False
        tennis_web = WEB_ROOT / "api_velora_matchs_tennis.json"
        if tennis_web.is_file():
            try:
                raw_t = json.loads(tennis_web.read_text(encoding="utf-8"))
                tennis_json_vide = int((raw_t.get("meta") or {}).get("match_count") or 0) == 0
            except (OSError, json.JSONDecodeError):
                tennis_json_vide = True
        dump_sans_tennis = False
        if DUMP_HTML.is_file():
            try:
                from parser_winamax import winamax_state_missing_tennis

                dump_sans_tennis = winamax_state_missing_tennis(
                    json.loads(DUMP_HTML.read_text(encoding="utf-8"))
                )
            except (OSError, json.JSONDecodeError):
                pass
        force_tennis_scrape = tennis_json_vide and dump_sans_tennis
        force_scrape_foot = _aucun_match_foot_a_venir_ci()
        if force_scrape_foot:
            log(
                "CI : 0 match Foot à venir en JSON — scrape Winamax forcé "
                "(VELORA_PROXY_URL proxy France requis sur GitHub Actions).",
            )
        if force_tennis_scrape:
            log(
                "CI : tennis absent du dump/JSON — scrape Winamax forcé "
                "(ignore repli premium / dump récent).",
            )
        if not force_scrape_foot and not force_tennis_scrape:
            if mode in ("premium", "premium_web"):
                if _matchs_json_besoin_regeneration() and _dump_utilisable_pour_parser(DUMP_HTML):
                    if mode == "premium_web":
                        _synchroniser_premium_depuis_web()
                    log(
                        "CI : premium récent mais matchs périmés — "
                        "reprise au parser depuis dump Winamax (matchs à venir présents).",
                    )
                    debut = 1
                elif not _matchs_json_besoin_regeneration():
                    if mode == "premium_web":
                        _synchroniser_premium_depuis_web()
                    log(
                        "CI : scraper Winamax ignoré — premium et matchs à jour "
                        f"(fichiers < {os.environ.get('VELORA_CI_SCRAPER_MAX_AGE_H', '6')} h). "
                        "Résolution PMU/Foot et publication communauté continuent.",
                    )
                    return True
            elif mode == "dump" and _dump_utilisable_pour_parser(DUMP_HTML):
                log(
                    "CI : dump HTML récent avec matchs à venir — "
                    "reprise à l'étape parser (sans re-scraper Winamax).",
                )
                debut = 1
            elif mode in ("matchs", "matchs_web"):
                if mode == "matchs_web":
                    _synchroniser_premium_depuis_web()
                log("CI : matchs JSON récents — reprise au sniper uniquement.")
                debut = 2

    for script, label, extra in etapes[debut:]:
        if run_step(script, label, extra):
            log(f"{label} — terminée.\n")
            continue

        if not _ci_scraper_optionnel():
            return False

        mode = _mode_repli_scraper_ci()
        if script == "winamax_dump.py":
            dump_ok = _dump_utilisable_pour_parser(DUMP_HTML)
            if dump_ok and (
                mode in ("premium", "premium_web", "dump", "matchs", "matchs_web")
                or _matchs_json_besoin_regeneration()
            ):
                log(
                    "CI : échec scrape Winamax — reprise au parser sur dump_winamax_html.json existant.",
                )
                if not run_step("parser_winamax.py", etapes[1][1], etapes[1][2]):
                    if mode in ("premium", "premium_web"):
                        if mode == "premium_web":
                            _synchroniser_premium_depuis_web()
                        log(
                            "CI : parser en échec — conservation du premium existant, "
                            "suite du pipeline (résultats + communauté).",
                        )
                        return True
                    return False
                log(f"{etapes[1][1]} — terminée.\n")
                if not run_step("parser_winamax_tennis.py", etapes[2][1], etapes[2][2]):
                    return False
                log(f"{etapes[2][1]} — terminée.\n")
                if not run_step("winamax_sniper.py", etapes[3][1], etapes[3][2]):
                    return False
                log(f"{etapes[3][1]} — terminée.\n")
                return True
            if mode in ("premium", "premium_web"):
                if mode == "premium_web":
                    _synchroniser_premium_depuis_web()
                log(
                    "CI : géoblocage Winamax — conservation du premium existant, "
                    "suite du pipeline (résultats + communauté).",
                )
                return True
            if mode == "dump":
                log("CI : échec dump mais dump_winamax_html.json récent — étape parser.")
                if not run_step("parser_winamax.py", etapes[1][1], etapes[1][2]):
                    return False
                log(f"{etapes[1][1]} — terminée.\n")
                if not run_step("parser_winamax_tennis.py", etapes[2][1], etapes[2][2]):
                    return False
                log(f"{etapes[2][1]} — terminée.\n")
                if not run_step("winamax_sniper.py", etapes[3][1], etapes[3][2]):
                    return False
                log(f"{etapes[3][1]} — terminée.\n")
                return True

        log_error(
            label,
            "Scraper Winamax en échec et aucun JSON récent pour le repli CI. "
            "Ajoutez le secret GitHub VELORA_PROXY_URL (proxy France) ou relancez "
            "run_all.py en local.",
        )
        return False

    if not rebuild_foot_conseils_json():
        log("Attention : rebuild conseils Foot en échec (cartes sans BTTS/O/U/DC).")

    return True


def rebuild_foot_conseils_json() -> bool:
    """Recalcule conseils_intelligents (BTTS, O/U, DC…) dans api_velora_matchs.json."""
    script = (ROOT / "scripts" / "rebuild_conseils_json.py").resolve()
    if not script.is_file():
        log("[conseils] scripts/rebuild_conseils_json.py absent — ignoré.")
        return True
    if not MATCHS_JSON.is_file():
        log("[conseils] api_velora_matchs.json absent — ignoré.")
        return True
    return run_step("scripts/rebuild_conseils_json.py", "Recalcul conseils intelligents Foot (BTTS, O/U, DC…)", None)


def post_traitement_communaute() -> bool:
    """Résolution PMU + Foot + vitrine communauté (ROI / bénéfices)."""
    ok = run_web_script(
        "resolver_pmu_archives.py",
        "Résolution archives PMU (arrivées officielles + rapports définitifs)",
    )
    ok = run_web_script(
        "velora_archiver_foot.py",
        "Archives Foot (scores Winamax + résolution EN_ATTENTE + ROI)",
    ) and ok
    ok = run_web_script(
        "publish_communaute.py",
        "Publication api_velora_communaute.json (PMU + Foot)",
    ) and ok
    return ok


def main() -> int:
    os.chdir(ROOT)

    try:
        init_deploy_paths()
    except FileNotFoundError as e:
        log_error("Configuration déploiement", str(e))
        log(str(e))
        return 1

    assert WEB_ROOT is not None
    log("=== Velora Engine — pipeline complet (scraper + résultats + déploiement) ===")
    log(f"Racine scraper     : {ROOT}")
    log(f"Projet web (cible) : {WEB_ROOT}")
    log(f"  index.html       : {(WEB_ROOT / 'index.html').resolve()}")
    log(
        f"  CI / headless    : GITHUB_ACTIONS={os.environ.get('GITHUB_ACTIONS', '')!r} "
        f"VELORA_HEADLESS={os.environ.get('VELORA_HEADLESS', '(auto)')!r}\n"
    )

    wait_for_internet()

    if not executer_phase_scraper():
        log(f"\nPipeline interrompu — phase scraper Winamax")
        log(f"Consultez {ERROR_LOG} pour le détail.")
        return 1

    log("Étape 4/5 : déploiement local vers projet web")
    if not deploy_json():
        log("\nPipeline interrompu après : déploiement local")
        return 1
    log("Étape 4/5 — terminée.\n")

    if not post_traitement_communaute():
        log(
            "Attention : post-traitement PMU/Foot/communauté partiel ou en échec "
            "(voir error_log.txt — vérifier .env Supabase pour resolver_pmu_archives.py).",
        )
    else:
        if COMMUNAUTE_DEPLOY and COMMUNAUTE_DEPLOY.is_file():
            log(f"Communauté : {_fmt_file_info(COMMUNAUTE_DEPLOY)}")

    log("Étape 5/5 : mise en ligne (Git push → Vercel)")
    if not push_vercel_git():
        log("\nPipeline interrompu après : push Git")
        return 1
    log("Étape 5/5 — terminée.\n")

    log("=== Pipeline terminé avec succès ===")
    log("Données déployées vers le projet web et poussées vers le dépôt Git.")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:
        log_error("Erreur fatale pipeline", str(e), traceback.format_exc())
        log(f"\nÉchec critique — voir {ERROR_LOG}")
        code = 1
    sys.exit(code)
