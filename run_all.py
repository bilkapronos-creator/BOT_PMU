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
CI : .github/workflows/velora_cron.yml (toutes les 2 h + 6 h UTC).

Surcharge : VELORA_WEB_DIR=C:\\chemin\\vers\\web
Désactiver le push : VELORA_SKIP_GIT_PUSH=1
"""
from __future__ import annotations

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
MATCHS_JSON = ROOT / "api_velora_matchs.json"
PREMIUM_SRC = ROOT / "api_velora_premium.json"
GIT_COMMIT_MSG = "Mise à jour automatique Velora Data"
GIT_JSON_FILES = (
    "api_velora_matchs.json",
    "api_velora_premium.json",
    "api_velora_communaute.json",
    "velora_foot_resultats.json",
    "velora_archives_foot.json",
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


def init_deploy_paths() -> None:
    global WEB_ROOT, MATCHS_DEPLOY, PREMIUM_DEPLOY, COMMUNAUTE_DEPLOY
    WEB_ROOT = resolve_web_project_dir()
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

        r_add = run_git(["git", "add", *to_commit])
        if r_add.returncode != 0:
            log_error(label, "git add a échoué", f"{r_add.stdout}\n{r_add.stderr}")
            return False
        log(f"  git add {' '.join(to_commit)}")

        r_commit = run_git(["git", "commit", "-m", GIT_COMMIT_MSG])
        if r_commit.returncode != 0:
            if _commit_sans_modifications(r_commit):
                log("Aucune modification à pusher")
                log_success(label, "Aucune modification à pusher — pipeline OK sans push")
                return True
            log_error(label, "git commit a échoué", f"{r_commit.stdout}\n{r_commit.stderr}")
            return False
        log(f'  git commit -m "{GIT_COMMIT_MSG}"')

        r_push = run_git(["git", "push"])
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

    steps = [
        ("winamax_dump.py", "Étape 1/5 : extraction SSR Winamax", None),
        ("parser_winamax.py", "Étape 2/5 : structuration JSON", None),
        (
            "winamax_sniper.py",
            f"Étape 3/5 : enrichissement sniper (max {SNIPER_LIMIT})",
            {"SNIPER_LIMIT": SNIPER_LIMIT},
        ),
    ]

    for script, label, extra in steps:
        if not run_step(script, label, extra):
            log(f"\nPipeline interrompu après : {label}")
            log(f"Consultez {ERROR_LOG} pour le détail.")
            return 1
        log(f"{label} — terminée.\n")

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
