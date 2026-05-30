"""
Archivage Foot — validation des pronos terminés (même logique financière que PMU).
Produit velora_archives_foot.json + api_velora_communaute.json (bloc foot + global).
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from velora_finance import MISE_UNITAIRE, calculer_resultat_financier
ROOT = Path(__file__).resolve().parent
ARCHIVES_FOOT_PATH = ROOT / "velora_archives_foot.json"
RESULTATS_PATH = ROOT / "velora_foot_resultats.json"
SNAPSHOT_PREMIUM_PATH = ROOT / "api_velora_premium.json"
COMMUNAUTE_PATH = ROOT / "api_velora_communaute.json"
VEILLE_SCORES_UNIQUEMENT = os.environ.get("VELORA_FOOT_VEILLE_ONLY", "1").strip() not in (
    "0",
    "false",
    "False",
)


def _scraper_root() -> Path | None:
    env = os.environ.get("VELORA_SCRAPER_DIR", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_dir():
            return p
    sibling = ROOT.parent / "velora-scraper-winamax"
    if sibling.is_dir():
        return sibling.resolve()
    return None


def _importer_fetch_winamax():
    scraper = _scraper_root()
    if scraper and str(scraper) not in sys.path:
        sys.path.insert(0, str(scraper))
    from winamax_foot_results import (  # noqa: PLC0415
        fetch_winamax_results,
        fusionner_resultats_fichier,
        match_devrait_avoir_score,
    )

    return fetch_winamax_results, fusionner_resultats_fichier, match_devrait_avoir_score


def _safe_float(val) -> float | None:
    try:
        if val is None:
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def parse_score_reel(resultat_reel) -> tuple[int, int] | None:
    """Accepte '2-1', '2 - 1', ou {domicile, exterieur} / score_dom / score_ext."""
    if resultat_reel is None:
        return None
    if isinstance(resultat_reel, dict):
        dom = resultat_reel.get("domicile") or resultat_reel.get("dom") or resultat_reel.get("home")
        ext = resultat_reel.get("exterieur") or resultat_reel.get("ext") or resultat_reel.get("away")
        try:
            return int(dom), int(ext)
        except (TypeError, ValueError):
            return None
    text = str(resultat_reel).strip().replace("–", "-")
    nums = [int(x) for x in re.findall(r"\d+", text)]
    if len(nums) >= 2:
        return nums[0], nums[1]
    return None


def _issue_1n2(dom: int, ext: int) -> str:
    if dom > ext:
        return "1"
    if dom == ext:
        return "N"
    return "2"


def _pick_1n2(match: dict) -> str | None:
    cotes = match.get("cotes") or {}
    conseil = str(match.get("conseil") or "").lower()
    if "dom" in conseil or "favori domicile" in conseil:
        return "1"
    if "ext" in conseil or "extérieur" in conseil or "exterieur" in conseil:
        return "2"
    if "nul" in conseil:
        return "N"
    c1 = _safe_float(cotes.get("1"))
    c2 = _safe_float(cotes.get("2"))
    if c1 is not None and c2 is not None:
        return "1" if c1 <= c2 else "2"
    return None


def _cote_btts(match: dict, oui: bool) -> float | None:
    btts = match.get("btts")
    if isinstance(btts, dict):
        return _safe_float(btts.get("oui") if oui else btts.get("non"))
    return None


def _cote_over25(match: dict, plus: bool) -> float | None:
    ou = match.get("over_under_25")
    if isinstance(ou, dict):
        return _safe_float(ou.get("plus") if plus else ou.get("moins"))
    ms = match.get("marches_supplementaires") or {}
    pm = (ms.get("plus_moins_buts") or {}).get("2.5")
    if isinstance(pm, dict):
        return _safe_float(pm.get("plus_cote") if plus else pm.get("moins_cote"))
    return None


def _cote_buteur(match: dict) -> float | None:
    buteurs = match.get("buteurs")
    if isinstance(buteurs, list) and buteurs:
        return _safe_float(buteurs[0].get("cote"))
    ms = match.get("marches_supplementaires") or {}
    bm = ms.get("buteur_match")
    if isinstance(bm, list) and bm:
        return _safe_float(bm[0].get("cote"))
    detail = str(match.get("opportunite_detail") or "")
    m = re.search(r"@\s*(\d+(?:\.\d+)?)", detail)
    if m:
        return _safe_float(m.group(1))
    return None


def _cote_score_exact(match: dict) -> float | None:
    scores = match.get("score_exact")
    if isinstance(scores, list) and scores:
        return _safe_float(scores[0].get("cote"))
    detail = str(match.get("opportunite_detail") or "")
    m = re.search(r"@\s*(\d+(?:\.\d+)?)", detail)
    if m:
        return _safe_float(m.group(1))
    return None


def _score_exact_attendu(match: dict) -> tuple[int, int] | None:
    detail = str(match.get("opportunite_detail") or match.get("conseil") or "")
    nums = [int(x) for x in re.findall(r"\d+", detail.replace("–", "-").split("@")[0])]
    if len(nums) >= 2:
        return nums[0], nums[1]
    scores = match.get("score_exact")
    if isinstance(scores, list) and scores:
        label = str(scores[0].get("score") or "")
        parsed = parse_score_reel(label)
        if parsed:
            return parsed
    return None


def valider_foot(match: dict, resultat_reel) -> dict | None:
    """
    Valide un pronostic Foot vs résultat réel.
    Retourne l'archive complète (statut, type_pari_foot, financier) ou None si score absent.
    """
    score = parse_score_reel(resultat_reel)
    if score is None:
        return None

    dom, ext = score
    total_buts = dom + ext
    marche = str(
        match.get("opportunite_type") or match.get("value_bet_type") or "1n2"
    ).strip().lower()

    gagne = False
    type_pari = marche
    cote: float | None = None
    selection = ""

    if marche == "btts":
        oui = dom > 0 and ext > 0
        detail = str(match.get("opportunite_detail") or "").lower()
        pick_oui = "non" not in detail and ("oui" in detail or "btts" in detail)
        gagne = oui if pick_oui else not oui
        cote = _cote_btts(match, pick_oui)
        type_pari = "BTTS Oui" if pick_oui else "BTTS Non"
        selection = type_pari

    elif marche == "over_25":
        detail = str(match.get("opportunite_detail") or "").lower()
        pick_plus = "moins" not in detail and ("+" in detail or "over" in detail or "plus" in detail)
        gagne = (total_buts > 2) if pick_plus else (total_buts < 3)
        cote = _cote_over25(match, pick_plus)
        type_pari = "Over 2.5" if pick_plus else "Under 2.5"
        selection = type_pari

    elif marche == "buteur":
        # Sans liste de buteurs réels : non validable automatiquement
        return None

    elif marche == "score_exact":
        attendu = _score_exact_attendu(match)
        if attendu is None:
            return None
        gagne = (dom, ext) == attendu
        cote = _cote_score_exact(match)
        type_pari = f"Score exact {dom}-{ext}"
        selection = type_pari

    else:
        issue = _issue_1n2(dom, ext)
        pick = _pick_1n2(match)
        if not pick:
            return None
        gagne = pick == issue
        cote = _safe_float((match.get("cotes") or {}).get(pick))
        labels = {"1": "Domicile", "N": "Nul", "2": "Extérieur"}
        type_pari = f"1N2 {labels.get(pick, pick)}"
        selection = type_pari
        marche = "1n2"

    if not gagne and type_pari:
        type_pari = "Perdu"

    financier = calculer_resultat_financier(gagne, cote)

    return {
        "sport": "foot",
        "id_match": str(match.get("id_match") or ""),
        "equipe_domicile": match.get("equipe_domicile") or "?",
        "equipe_exterieur": match.get("equipe_exterieur") or "?",
        "date_match": match.get("date_match") or "",
        "match_start_ts": match.get("match_start_ts"),
        "opportunite_type": marche,
        "opportunite_detail": match.get("opportunite_detail") or match.get("conseil") or "",
        "marche": marche,
        "selection": selection,
        "score_final": f"{dom}-{ext}",
        "statut": "Terminée",
        "reussi_foot": gagne,
        "type_pari_foot": type_pari if gagne else "Perdu",
        "cote_jouee": cote,
        "financier": financier,
        "timestamp": int(datetime.now(tz=timezone.utc).timestamp() * 1000),
        "valide_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def _lire_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _ecrire_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _index_archives(archives: list[dict]) -> dict[str, dict]:
    return {str(a.get("id_match")): a for a in archives if a.get("id_match")}


def _score_deja_enregistre(resultats: dict, mid: str) -> bool:
    entree = resultats.get(mid) or resultats.get(int(mid)) if mid.isdigit() else None
    if entree is None and isinstance(resultats.get("matchs"), dict):
        entree = resultats["matchs"].get(mid)
    return parse_score_reel(entree) is not None


def _ids_matchs_a_recuperer(
    snapshots: list[dict],
    par_id: dict[str, dict],
    resultats: dict,
) -> list[str]:
    """Matchs premium non archivés « Terminée » dont le score peut être sur Winamax."""
    try:
        _, _, match_devrait_avoir_score = _importer_fetch_winamax()
    except Exception as exc:
        print(f"[archiver-foot] Sync Winamax indisponible : {exc}")
        return []

    ids: list[str] = []
    for match in snapshots:
        if not isinstance(match, dict):
            continue
        mid = str(match.get("id_match") or "").strip()
        if not mid:
            continue
        if par_id.get(mid, {}).get("statut") == "Terminée":
            continue
        if not match_devrait_avoir_score(
            match,
            veille_uniquement=VEILLE_SCORES_UNIQUEMENT,
        ):
            continue
        if _score_deja_enregistre(resultats, mid):
            continue
        ids.append(mid)
    return ids


def synchroniser_scores_winamax() -> dict[str, Any]:
    """
    Étape A — récupère les scores finaux Winamax et met à jour velora_foot_resultats.json.
    Les échecs sont ignorés (match revérifié au prochain lancement).
    """
    stats = {"demandes": 0, "recuperes": 0, "erreur": None}
    snapshots = _lire_json(SNAPSHOT_PREMIUM_PATH, [])
    if not isinstance(snapshots, list):
        snapshots = []

    archives = _lire_json(ARCHIVES_FOOT_PATH, [])
    par_id = _index_archives(archives)
    resultats = _lire_json(RESULTATS_PATH, {})
    if not isinstance(resultats, dict):
        resultats = {}

    ids = _ids_matchs_a_recuperer(snapshots, par_id, resultats)
    stats["demandes"] = len(ids)
    if not ids:
        print("[archiver-foot] Aucun match en attente de score Winamax.")
        return stats

    try:
        fetch_winamax_results, fusionner_resultats_fichier, _ = _importer_fetch_winamax()
        scores = fetch_winamax_results(ids)
        stats["recuperes"] = len(scores)
        if scores:
            fusionner_resultats_fichier(RESULTATS_PATH, scores)
            print(
                f"[archiver-foot] Scores Winamax : {len(scores)}/{len(ids)} "
                f"→ {RESULTATS_PATH.name}"
            )
        else:
            print(
                f"[archiver-foot] Winamax : 0/{len(ids)} score(s) disponible(s) "
                "(prochain run)."
            )
    except Exception as exc:
        stats["erreur"] = str(exc)
        print(f"[archiver-foot] Échec sync Winamax (validation continue) : {exc}")
    return stats


def traiter_archives_foot() -> dict[str, Any]:
    sync_stats = synchroniser_scores_winamax()

    archives = _lire_json(ARCHIVES_FOOT_PATH, [])
    par_id = _index_archives(archives)
    resultats = _lire_json(RESULTATS_PATH, {})
    if not isinstance(resultats, dict):
        resultats = {}

    snapshots = _lire_json(SNAPSHOT_PREMIUM_PATH, [])
    if not isinstance(snapshots, list):
        snapshots = []

    nouvelles = 0
    for match in snapshots:
        mid = str(match.get("id_match") or "")
        if not mid:
            continue
        if mid in par_id and par_id[mid].get("statut") == "Terminée":
            continue
        res = resultats.get(mid) or resultats.get(int(mid)) if mid.isdigit() else None
        if res is None and isinstance(resultats.get("matchs"), dict):
            res = resultats["matchs"].get(mid)
        if res is None:
            continue
        archive = valider_foot(match, res)
        if not archive:
            continue
        par_id[mid] = archive
        nouvelles += 1

    for mid, res in resultats.items():
        if str(mid) in ("matchs", "meta"):
            continue
        key = str(mid)
        if key in par_id and par_id[key].get("statut") == "Terminée":
            continue
        match = par_id.get(key) or {"id_match": key}
        if not match.get("equipe_domicile"):
            for m in snapshots:
                if str(m.get("id_match")) == key:
                    match = m
                    break
        archive = valider_foot(match, res)
        if archive:
            par_id[key] = archive
            nouvelles += 1

    archives_finales = sorted(
        par_id.values(),
        key=lambda a: a.get("match_start_ts") or a.get("timestamp") or 0,
        reverse=True,
    )
    _ecrire_json(ARCHIVES_FOOT_PATH, archives_finales)

    from publish_communaute import construire_communaute

    communaute = construire_communaute(
        {
            "archives_foot": len(archives_finales),
            "nouvelles_validations": nouvelles,
            "sync_winamax": sync_stats,
        }
    )
    _ecrire_json(COMMUNAUTE_PATH, communaute)
    return communaute


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("[archiver-foot] Étape A : scores Winamax → velora_foot_resultats.json")
    print("[archiver-foot] Étape B : validation valider_foot() + communauté")
    communaute = traiter_archives_foot()
    foot = communaute.get("foot") or {}
    print(
        f"[archiver-foot] Foot : {foot.get('victoires')}/{foot.get('total')} "
        f"({foot.get('taux')}%) · ROI {foot.get('roi_pct')}% · "
        f"P&L {foot.get('profit_net')} € → {COMMUNAUTE_PATH.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
