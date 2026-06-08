"""
Archivage Foot — validation des pronos terminés (même logique financière que PMU).
Produit velora_archives_foot.json + api_velora_communaute.json (bloc foot + global).
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from foot_team_fuzzy import score_foot_plausible
from velora_finance import MISE_UNITAIRE, calculer_resultat_financier

TZ_PARIS = ZoneInfo("Europe/Paris")
ROOT = Path(__file__).resolve().parent
ARCHIVES_FOOT_PATH = ROOT / "velora_archives_foot.json"
RESULTATS_PATH = ROOT / "velora_foot_resultats.json"
SNAPSHOT_PREMIUM_PATH = ROOT / "api_velora_premium.json"
COMMUNAUTE_PATH = ROOT / "api_velora_communaute.json"
MATCHS_JSON_PATH = ROOT / "api_velora_matchs.json"
VEILLE_SCORES_UNIQUEMENT = os.environ.get("VELORA_FOOT_VEILLE_ONLY", "0").strip() not in (
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
    # Monorepo : winamax_foot_results.py à la racine du même dépôt
    if (ROOT / "winamax_foot_results.py").is_file():
        return ROOT.resolve()
    sibling = ROOT.parent / "velora-scraper-winamax"
    if (sibling / "winamax_foot_results.py").is_file():
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
        dom = resultat_reel.get("domicile")
        if dom is None:
            dom = resultat_reel.get("dom", resultat_reel.get("home"))
        ext = resultat_reel.get("exterieur")
        if ext is None:
            ext = resultat_reel.get("ext", resultat_reel.get("away"))
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


def _est_pronostic_velora(match: dict) -> bool:
    """True si le match fait partie des pronos Velora (premium / value), pas tout le catalogue."""
    if match.get("is_opportunite") is True:
        return True
    ot = str(match.get("opportunite_type") or "").strip()
    if ot and ot.lower() not in ("—", "none", "-", ""):
        return True
    vbt = str(match.get("value_bet_type") or "").strip().lower()
    if vbt and vbt not in ("—", "none", "-", ""):
        return True
    free = match.get("free_analysis") or {}
    pp = match.get("primary_pick") or free.get("primary_pick")
    if isinstance(pp, dict) and str(pp.get("market") or "").strip():
        return True
    if match.get("pronostic_1n2") or match.get("velora_pick_1n2") or free.get("pronostic_1n2"):
        return True
    try:
        if float(match.get("velora_score") or 0) >= 68:
            return True
    except (TypeError, ValueError):
        pass
    blob = _blob_conseil(match)
    if "value bet" in blob or "privilégier le +2.5" in blob or "privilegier le +2.5" in blob:
        return True
    return False


def _normaliser_archive_importee(archive: dict) -> dict:
    """Complète marche / opportunite_type pour les imports localStorage."""
    if not isinstance(archive, dict):
        return archive
    out = dict(archive)
    marche = _marche_effectif(out)
    out["marche"] = marche
    if not out.get("opportunite_type"):
        out["opportunite_type"] = marche
    ts = out.get("match_start_ts")
    if ts is not None:
        try:
            t = float(ts)
            if t > 1e12:
                out["match_start_ts"] = int(t)
            else:
                out["match_start_ts"] = int(t)
        except (TypeError, ValueError):
            pass
    if out.get("match_start_ts") is None:
        kickoff = _kickoff_match(out)
        if kickoff is not None:
            out["match_start_ts"] = int(kickoff.timestamp())
    return out


def _blob_conseil(match: dict) -> str:
    return str(
        match.get("opportunite_detail") or match.get("conseil") or ""
    ).lower()


def _pick_ou25(match: dict) -> str | None:
    """plus | moins — aligné inferVeloraOu25Pick (index.html)."""
    opp = str(match.get("opportunite_type") or match.get("value_bet_type") or "").lower()
    if opp == "over_25":
        return "plus"
    blob = _blob_conseil(match)
    if "under" in blob or "moins de 2.5" in blob or "-2.5" in blob or "−2.5" in blob:
        return "moins"
    if (
        "+2.5" in blob
        or "+2,5" in blob
        or "over 2.5" in blob
        or "privilégier le +2.5" in blob
        or "privilegier le +2.5" in blob
    ):
        return "plus"
    return None


def _pick_1n2(match: dict) -> str | None:
    """1 | N | 2 | dc_1x | dc_x2 — pronostic modèle (pas le value bet primary_pick)."""
    free = match.get("free_analysis") or {}
    pick = (
        match.get("pronostic_1n2")
        or match.get("velora_pick_1n2")
        or free.get("pronostic_1n2")
    )
    if pick in ("1", "N", "2", "dc_1x", "dc_x2"):
        return pick

    opp = str(match.get("opportunite_type") or match.get("value_bet_type") or "").lower()
    if opp in ("buteur", "score_exact", "btts", "over_25"):
        return None

    blob = _blob_conseil(match)
    if "dc 1x" in blob or ("double chance" in blob and "1x" in blob):
        return "dc_1x"
    if "dc x2" in blob or ("double chance" in blob and "x2" in blob):
        return "dc_x2"

    if "value bet" in blob:
        if re.search(r"\bext\b", blob) or "ext +" in blob:
            return "2"
        if re.search(r"\bdom\b", blob):
            return "1"
        if re.search(r"\bnul\b", blob):
            return "N"

    conseil = str(match.get("conseil") or "").lower()
    if "nul" in conseil and "+2.5" not in conseil and "+2,5" not in conseil:
        return "N"
    if re.search(r"\bdom\b", blob) and "+2.5" not in blob and "+2,5" not in blob:
        return "1"
    if re.search(r"\bext\b", blob) and "+2.5" not in blob and "+2,5" not in blob:
        return "2"

    cotes = match.get("cotes") or {}
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


def _marche_effectif(match: dict) -> str:
    """Type de pari (btts, over_25, 1n2, …) depuis opportunité ou conseil Velora."""
    marche = str(
        match.get("opportunite_type") or match.get("value_bet_type") or match.get("marche") or ""
    ).strip().lower()
    if marche in ("btts", "over_25", "1n2", "score_exact", "buteur"):
        return marche
    blob = str(
        match.get("opportunite_detail") or match.get("conseil") or ""
    ).lower()
    if "btts" in blob or "les deux équipes" in blob or "les deux equipes" in blob:
        return "btts"
    if re.search(
        r"[+-]\s*2[\.,]5|over\s*2[,\.]?5|under\s*2[,\.]?5|plus de 2[,\.]?5|moins de 2[,\.]?5|privil.gier le \+2",
        blob,
    ):
        return "over_25"
    if re.search(r"\b(dom|ext)\s*\+\s*2[\.,]5", blob):
        return "over_25"
    if "score exact" in blob or re.search(r"\d+\s*[-:]\s*\d+", blob):
        return "score_exact"
    if "buteur" in blob:
        return "buteur"
    return "1n2"


def _archive_score_invalide(archive: dict) -> bool:
    """True si score_final présent mais aberrant (ex. 19-15 via Google)."""
    parsed = parse_score_reel(archive.get("score_final"))
    if parsed is None:
        return False
    dom, ext = parsed
    return not score_foot_plausible(dom, ext)


def _archive_doit_etre_revalidee(archive: dict) -> bool:
    """Score aberrant ou différent de la table de référence (correction batch)."""
    if _archive_score_invalide(archive):
        return True
    try:
        from foot_scores_reference import REFERENCE_BY_ID  # noqa: PLC0415
    except ImportError:
        return False
    mid = str(archive.get("id_match") or "").strip()
    ref = REFERENCE_BY_ID.get(mid)
    if not ref:
        return False
    cur = parse_score_reel(archive.get("score_final"))
    if cur is None:
        return False
    attendu = (int(ref["domicile"]), int(ref["exterieur"]))
    return cur != attendu


def _reinitialiser_archive_en_attente(archive: dict) -> dict:
    """Remet une entrée en EN_ATTENTE pour re-fetch / re-validation."""
    out = dict(archive)
    out["score_final"] = None
    out["statut"] = "EN_ATTENTE"
    out["statut_pari"] = "EN_ATTENTE"
    out["reussi_foot"] = None
    out["type_pari_foot"] = None
    out["cote_jouee"] = None
    out["financier"] = None
    out["profit"] = None
    out.pop("valide_at", None)
    return out


def valider_foot(match: dict, resultat_reel) -> dict | None:
    """
    Valide un pronostic Foot vs résultat réel.
    Retourne l'archive complète (statut, type_pari_foot, financier) ou None si score absent.
    """
    score = parse_score_reel(resultat_reel)
    if score is None:
        return None

    dom, ext = score
    if not score_foot_plausible(dom, ext):
        print(
            f"[resolver-foot] Score ignoré (aberrant) : "
            f"{match.get('equipe_domicile')} {dom}-{ext} {match.get('equipe_exterieur')}"
        )
        return None
    total_buts = dom + ext
    marche = _marche_effectif(match)

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
        pick_ou = _pick_ou25(match)
        if pick_ou is None:
            pick_ou = "plus"
        pick_plus = pick_ou == "plus"
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
        ad, ae = attendu
        type_pari = f"Score exact {ad}-{ae}"
        selection = type_pari

    else:
        issue = _issue_1n2(dom, ext)
        pick = _pick_1n2(match)
        if not pick:
            return None
        if pick == "dc_1x":
            gagne = issue in ("1", "N")
            cote = _safe_float((match.get("cotes") or {}).get("1"))
            type_pari = "Double chance 1X"
        elif pick == "dc_x2":
            gagne = issue in ("N", "2")
            cote = _safe_float((match.get("cotes") or {}).get("2"))
            type_pari = "Double chance X2"
        else:
            gagne = pick == issue
            cote = _safe_float((match.get("cotes") or {}).get(pick))
            labels = {"1": "Domicile", "N": "Nul", "2": "Extérieur"}
            type_pari = f"1N2 {labels.get(pick, pick)}"
        selection = type_pari
        marche = "1n2"

    type_pari_foot = type_pari if gagne else "Perdu"

    financier = calculer_resultat_financier(gagne, cote)
    statut_pari = "GAGNANT" if gagne else "PERDANT"

    return {
        "sport": "foot",
        "id_match": str(match.get("id_match") or ""),
        "equipe_domicile": match.get("equipe_domicile") or "?",
        "equipe_exterieur": match.get("equipe_exterieur") or "?",
        "date_match": match.get("date_match") or "",
        "match_start_ts": match.get("match_start_ts"),
        "conseil": match.get("conseil") or "",
        "cotes": match.get("cotes") or {},
        "indice_velora": match.get("indice_velora"),
        "opportunite_type": marche,
        "opportunite_detail": match.get("opportunite_detail") or match.get("conseil") or "",
        "marche": marche,
        "selection": selection,
        "score_final": f"{dom}-{ext}",
        "statut": "Terminée",
        "statut_pari": statut_pari,
        "reussi_foot": gagne,
        "type_pari_foot": type_pari_foot,
        "cote_jouee": cote,
        "financier": financier,
        "profit": financier.get("profit"),
        "timestamp": int(datetime.now(tz=timezone.utc).timestamp() * 1000),
        "valide_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def _normaliser_statut_pari(val: Any) -> str:
    s = str(val or "").strip().upper().replace("É", "E")
    return s


def _normaliser_statut_brut(val: Any) -> str:
    """Unifie EN_ATTENTE / EN ATTENTE / en attente / En attente du résultat…"""
    s = str(val or "").strip().upper().replace("É", "E")
    s = re.sub(r"\s+", "_", s)
    if s in ("EN_ATTENTE_DU_RESULTAT", "EN_ATTENTE_RESULTAT", "ATTENTE", "ATTENTE_DU_RESULTAT"):
        return "EN_ATTENTE"
    if s.startswith("EN_ATTENTE"):
        return "EN_ATTENTE"
    return s


def _archive_pret_pour_score(record: dict, now: datetime | None = None) -> bool:
    """Match terminé (coup d'envoi + marge) — utilise date_match si match_start_ts est null."""
    now = now or datetime.now(tz=TZ_PARIS)
    kickoff = _kickoff_match(record)
    if kickoff is None:
        return False
    try:
        from parser_winamax import MATCH_FINISHED_GRACE_MINUTES  # noqa: PLC0415
    except Exception:
        MATCH_FINISHED_GRACE_MINUTES = 120
    grace = kickoff + timedelta(minutes=MATCH_FINISHED_GRACE_MINUTES)
    return now >= grace


def _parse_date_match_paris(date_str: str) -> datetime | None:
    m = re.match(
        r"^(\d{2})/(\d{2})/(\d{4})\s+à\s+(\d{2}):(\d{2})$",
        str(date_str or "").strip(),
    )
    if not m:
        return None
    d, mo, y, h, mi = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi, tzinfo=TZ_PARIS)
    except ValueError:
        return None


def _kickoff_match(record: dict) -> datetime | None:
    """Coup d'envoi Paris (match_start_ts ou date_match)."""
    if not isinstance(record, dict):
        return None
    try:
        scraper = _scraper_root()
        if scraper and str(scraper) not in sys.path:
            sys.path.insert(0, str(scraper))
        from winamax_sniper import match_start_datetime  # noqa: PLC0415

        kickoff = match_start_datetime(record)
        if kickoff is not None:
            return kickoff
    except Exception:
        pass
    ts = record.get("match_start_ts")
    if ts is not None:
        try:
            t = float(ts)
            if t > 1e12:
                t /= 1000.0
            return datetime.fromtimestamp(t, tz=TZ_PARIS)
        except (TypeError, ValueError, OSError, OverflowError):
            pass
    return _parse_date_match_paris(str(record.get("date_match") or ""))


def _match_deja_joue(record: dict, now: datetime | None = None) -> bool:
    """True si le coup d'envoi + marge de fin est passé (pas un match futur)."""
    return _archive_pret_pour_score(record, now)


def _est_en_attente(archive: dict) -> bool:
    """True si le pari n'a pas encore été résolu (statut explicite ou sans score validé)."""
    if not isinstance(archive, dict):
        return False
    if _archive_score_invalide(archive):
        return True
    sp_fin = _normaliser_statut_pari(archive.get("statut_pari"))
    if (
        sp_fin in ("GAGNE", "PERDU", "GAGNANT", "PERDANT")
        and parse_score_reel(archive.get("score_final")) is not None
        and archive.get("reussi_foot") is not None
    ):
        return False
    st = _normaliser_statut_brut(archive.get("statut"))
    sp = _normaliser_statut_brut(archive.get("statut_pari"))
    if st == "EN_ATTENTE" or sp == "EN_ATTENTE":
        return True
    raw_st = str(archive.get("statut") or "").strip().lower()
    raw_sp = str(archive.get("statut_pari") or "").strip().lower()
    if raw_st in ("en attente", "en attente du resultat", "en attente du résultat"):
        return True
    if raw_sp in ("en attente", "en attente du resultat", "en attente du résultat"):
        return True
    if archive.get("reussi_foot") is not None and parse_score_reel(archive.get("score_final")):
        return False
    if parse_score_reel(archive.get("score_final")) is not None:
        sp2 = _normaliser_statut_pari(archive.get("statut_pari"))
        if sp2 in ("GAGNE", "PERDU", "GAGNANT", "PERDANT"):
            return False
    if archive.get("reussi_foot") is None and parse_score_reel(archive.get("score_final")) is None:
        return True
    return False


def _est_archive_terminee_validee(archive: dict) -> bool:
    """Seul cas archivable : match terminé, score Winamax validé, statut Gagné ou Perdu."""
    if not isinstance(archive, dict) or _est_en_attente(archive):
        return False
    if not _match_deja_joue(archive):
        return False
    if parse_score_reel(archive.get("score_final")) is None:
        return False
    sp = _normaliser_statut_pari(archive.get("statut_pari"))
    if sp not in ("GAGNE", "PERDU", "GAGNANT", "PERDANT"):
        return False
    if archive.get("reussi_foot") is None:
        return False
    st = str(archive.get("statut") or "").strip().upper()
    return st in ("TERMINÉE", "TERMINEE", "TERMINE")


def _match_coup_envoi_passe(record: dict, now: datetime | None = None) -> bool:
    """True dès que le coup d'envoi est passé (archivage EN_ATTENTE)."""
    now = now or datetime.now(tz=TZ_PARIS)
    kickoff = _kickoff_match(record)
    if kickoff is None:
        return False
    return now >= kickoff


def construire_archive_foot_en_attente(match: dict) -> dict:
    """Snapshot pronostic au coup d'envoi — résolu plus tard par resoudre_matchs_en_attente()."""
    marche = _marche_effectif(match)
    ts = match.get("match_start_ts")
    try:
        ts_int = int(float(ts)) if ts is not None else None
    except (TypeError, ValueError):
        ts_int = None
    if ts_int is None:
        kickoff = _kickoff_match(match)
        if kickoff is not None:
            ts_int = int(kickoff.timestamp() * 1000)
    return {
        "sport": "foot",
        "id_match": str(match.get("id_match") or ""),
        "equipe_domicile": match.get("equipe_domicile") or "?",
        "equipe_exterieur": match.get("equipe_exterieur") or "?",
        "date_match": match.get("date_match") or "",
        "match_start_ts": ts_int,
        "conseil": match.get("conseil") or "",
        "cotes": match.get("cotes") or {},
        "indice_velora": match.get("indice_velora"),
        "opportunite_type": marche,
        "opportunite_detail": match.get("opportunite_detail") or match.get("conseil") or "",
        "marche": marche,
        "selection": "",
        "score_final": None,
        "statut": "EN_ATTENTE",
        "statut_pari": "EN_ATTENTE",
        "reussi_foot": None,
        "type_pari_foot": None,
        "cote_jouee": None,
        "financier": None,
        "profit": None,
        "timestamp": ts_int or int(datetime.now(tz=timezone.utc).timestamp() * 1000),
    }


def _lire_resultat_pour_match(resultats: dict, mid: str):
    res = resultats.get(mid) or (resultats.get(int(mid)) if mid.isdigit() else None)
    if res is None and isinstance(resultats.get("matchs"), dict):
        res = resultats["matchs"].get(mid)
    parsed = parse_score_reel(res)
    if parsed is None:
        return None
    dom, ext = parsed
    if not score_foot_plausible(dom, ext):
        return None
    return res


def _match_snapshot_pour_id(snapshots: list, mid: str, fallback: dict | None = None) -> dict:
    for m in snapshots:
        if str(m.get("id_match")) == mid:
            return m
    return fallback or {"id_match": mid}


def _match_record_pour_id(
    mid: str,
    snapshots: list,
    catalogue: list,
    fallback: dict | None = None,
) -> dict:
    rec = _match_snapshot_pour_id(snapshots, mid, fallback)
    if _kickoff_match(rec) is not None:
        return rec
    for m in catalogue:
        if str(m.get("id_match")) == mid:
            return m
    return rec


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


def _candidat_match_sans_score(
    match: dict,
    resultats: dict,
    match_devrait_avoir_score,
) -> str | None:
    """Retourne id_match si le coup d'envoi est passé et le score absent du JSON."""
    if not isinstance(match, dict):
        return None
    mid = str(match.get("id_match") or "").strip()
    if not mid or _score_deja_enregistre(resultats, mid):
        return None
    if not match_devrait_avoir_score(match, veille_uniquement=VEILLE_SCORES_UNIQUEMENT):
        return None
    return mid


def _collecter_ids_matchs_sans_score(
    archives: list[dict],
    snapshots: list[dict],
    catalogue: list[dict],
    resultats: dict,
) -> list[str]:
    """
    Matchs archivés / premium / catalogue : date passée, pas encore de score dans
    velora_foot_resultats.json → à interroger sur Winamax.
    """
    try:
        _, _, match_devrait_avoir_score = _importer_fetch_winamax()
    except Exception as exc:
        print(f"[archiver-foot] Module Winamax indisponible : {exc}")
        return []

    ids: set[str] = set()

    for archive in archives:
        if not isinstance(archive, dict) or not _est_en_attente(archive):
            continue
        mid = str(archive.get("id_match") or "").strip()
        if not mid or _score_deja_enregistre(resultats, mid):
            continue
        if _archive_pret_pour_score(archive):
            ids.add(mid)
            continue
        if match_devrait_avoir_score(archive, veille_uniquement=VEILLE_SCORES_UNIQUEMENT):
            ids.add(mid)

    for match in snapshots:
        mid = _candidat_match_sans_score(match, resultats, match_devrait_avoir_score)
        if mid:
            ids.add(mid)

    for match in catalogue:
        mid = _candidat_match_sans_score(match, resultats, match_devrait_avoir_score)
        if mid:
            ids.add(mid)

    return sorted(ids)


def _assurer_archives_coup_envoi(
    snapshots: list[dict],
    par_id: dict[str, dict],
    now: datetime | None = None,
    catalogue: list[dict] | None = None,
) -> int:
    """Ajoute / met à jour les entrées EN_ATTENTE dès le coup d'envoi (pronos Velora uniquement)."""
    now = now or datetime.now(tz=TZ_PARIS)
    ajouts = 0
    sources: list[dict] = [m for m in snapshots if isinstance(m, dict)]
    if catalogue:
        deja = {str(m.get("id_match") or "").strip() for m in sources}
        for match in catalogue:
            if not isinstance(match, dict):
                continue
            mid = str(match.get("id_match") or "").strip()
            if mid and mid not in deja:
                sources.append(match)
                deja.add(mid)
    for match in sources:
        if not isinstance(match, dict) or not _est_pronostic_velora(match):
            continue
        mid = str(match.get("id_match") or "").strip()
        if not mid:
            continue
        existant = par_id.get(mid)
        if existant and _est_archive_terminee_validee(existant):
            continue
        if existant and _est_en_attente(existant):
            continue
        if not _match_coup_envoi_passe(match, now):
            continue
        par_id[mid] = construire_archive_foot_en_attente(match)
        ajouts += 1
    return ajouts


def _enrichir_match_pour_validation(archive: dict, snapshots: list, catalogue: list) -> dict:
    """Fusionne archive EN_ATTENTE + snapshot premium pour valider_foot."""
    mid = str(archive.get("id_match") or "")
    snap = _match_record_pour_id(mid, snapshots, catalogue, archive)
    out = {**archive}
    for cle in (
        "opportunite_type",
        "opportunite_detail",
        "conseil",
        "cotes",
        "btts",
        "over_under_25",
        "marches_supplementaires",
        "score_exact",
        "buteurs",
        "velora_pick_1n2",
        "indice_velora",
    ):
        val = snap.get(cle)
        if val is not None and val != "" and val != {} and val != []:
            out[cle] = val
    if not out.get("marche"):
        out["marche"] = _marche_effectif(out)
    return out


def _fetch_scores_cascade(
    ids: list[str],
    archives_par_id: dict[str, dict],
) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """
    Cascade : Winamax → TheSportsDB (fuzzy) → Playwright (SofaScore / Flashscore / Google).
    Retourne (scores, stats par source).
    """
    stats = {"winamax": 0, "reference": 0, "thesportsdb": 0, "scraper": 0}
    if not ids:
        return {}, stats

    def _filtrer_scores(bruts: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
        ok: dict[str, dict[str, int]] = {}
        for mid, sc in bruts.items():
            try:
                dom, ext = int(sc["domicile"]), int(sc["exterieur"])
            except (KeyError, TypeError, ValueError):
                continue
            if score_foot_plausible(dom, ext):
                ok[mid] = {"domicile": dom, "exterieur": ext}
            else:
                arch = archives_par_id.get(mid) or {}
                print(
                    f"[resolver-foot] Score rejeté {mid} "
                    f"({arch.get('equipe_domicile')} {dom}-{ext} {arch.get('equipe_exterieur')})"
                )
        return ok

    scores: dict[str, dict[str, int]] = {}
    try:
        fetch_scores, _, _ = _importer_fetch_winamax()
        scores = _filtrer_scores(fetch_scores(ids) or {})
        stats["winamax"] = len(scores)
        if scores:
            print(f"[resolver-foot] Winamax : {len(scores)}/{len(ids)} score(s)")
    except Exception as exc:
        print(f"[resolver-foot] Winamax indisponible : {exc}")

    manquants = [mid for mid in ids if mid not in scores]
    if manquants:
        try:
            from foot_scores_reference import fetch_scores_reference_batch  # noqa: PLC0415

            batch = [
                {
                    "id_match": mid,
                    "equipe_domicile": (archives_par_id.get(mid) or {}).get("equipe_domicile"),
                    "equipe_exterieur": (archives_par_id.get(mid) or {}).get("equipe_exterieur"),
                    "kickoff": _kickoff_match(archives_par_id.get(mid) or {}),
                }
                for mid in manquants
            ]
            ref = fetch_scores_reference_batch(batch)
            for mid, sc in ref.items():
                scores[mid] = sc
                stats["reference"] += 1
        except ImportError as exc:
            print(f"[resolver-foot] Référence scores indisponible : {exc}")

    manquants = [mid for mid in ids if mid not in scores]
    if manquants:
        try:
            from foot_results_fallback import fetch_score_thesportsdb  # noqa: PLC0415

            for mid in manquants:
                arch = archives_par_id.get(mid) or {}
                kickoff = _kickoff_match(arch)
                fb = fetch_score_thesportsdb(
                    str(arch.get("equipe_domicile") or ""),
                    str(arch.get("equipe_exterieur") or ""),
                    kickoff,
                )
                if fb and score_foot_plausible(fb["domicile"], fb["exterieur"]):
                    scores[mid] = fb
                    stats["thesportsdb"] += 1
            if stats["thesportsdb"]:
                print(
                    f"[resolver-foot] TheSportsDB (fuzzy) : "
                    f"{stats['thesportsdb']}/{len(manquants)} score(s)"
                )
        except ImportError as exc:
            print(f"[resolver-foot] TheSportsDB indisponible : {exc}")

    manquants = [mid for mid in ids if mid not in scores]
    if manquants:
        batch: list[dict] = []
        for mid in manquants:
            arch = archives_par_id.get(mid) or {}
            batch.append(
                {
                    "id_match": mid,
                    "equipe_domicile": arch.get("equipe_domicile"),
                    "equipe_exterieur": arch.get("equipe_exterieur"),
                    "kickoff": _kickoff_match(arch),
                }
            )
        try:
            from foot_results_scraper import fetch_scores_playwright_batch  # noqa: PLC0415

            scraped = _filtrer_scores(fetch_scores_playwright_batch(batch))
            for mid, sc in scraped.items():
                scores[mid] = sc
                stats["scraper"] += 1
        except ImportError as exc:
            print(f"[resolver-foot] Scraper Playwright indisponible : {exc}")
        except Exception as exc:
            print(f"[resolver-foot] Scraper Playwright échoué : {exc}")

    total = len(scores)
    if total < len(ids):
        print(
            f"[resolver-foot] {len(ids) - total} match(s) sans score après cascade "
            f"(Winamax {stats['winamax']}, TheSportsDB {stats['thesportsdb']}, "
            f"Scraper {stats['scraper']})"
        )
    else:
        print(
            f"[resolver-foot] Cascade OK : {total}/{len(ids)} "
            f"(Winamax {stats['winamax']}, TheSportsDB {stats['thesportsdb']}, "
            f"Scraper {stats['scraper']})"
        )
    return scores, stats


def _fetch_scores_avec_repli(
    ids: list[str],
    archives_par_id: dict[str, dict],
) -> dict[str, dict[str, int]]:
    """Alias — cascade complète."""
    scores, _ = _fetch_scores_cascade(ids, archives_par_id)
    return scores


def _purger_archives_non_velora(par_id: dict[str, dict], snapshots: list[dict]) -> int:
    """
    Désactivé : ne supprime plus les EN_ATTENTE du JSON (ex. Palmeiras hors premium actuel).
    Les entrées restent jusqu'à résolution par le resolver.
    """
    return 0


def _chemins_archives_foot() -> list[Path]:
    """Fichiers archives à lire (web/ prioritaire, pas la racine scraper sauf si identique)."""
    chemins = [ARCHIVES_FOOT_PATH.resolve()]
    scraper = _scraper_root()
    if scraper:
        alt = (scraper / "web" / "velora_archives_foot.json").resolve()
        if alt.is_file() and alt not in chemins:
            chemins.append(alt)
        racine = (scraper / "velora_archives_foot.json").resolve()
        if racine.is_file() and racine not in chemins:
            print(
                f"[resolver-foot] Attention : {racine.name} à la racine scraper ignoré "
                f"(utiliser {ARCHIVES_FOOT_PATH.resolve()})"
            )
    env = os.environ.get("VELORA_ARCHIVES_FOOT_PATH", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_file() and p not in chemins:
            chemins.append(p)
    return chemins


def _charger_archives_foot() -> list[dict]:
    if not ARCHIVES_FOOT_PATH.is_file():
        print(f"[resolver-foot] Fichier absent : {ARCHIVES_FOOT_PATH.resolve()}")
        return []
    data = _lire_json(ARCHIVES_FOOT_PATH, [])
    if not isinstance(data, list):
        return []
    return [_normaliser_archive_importee(a) for a in data if isinstance(a, dict)]


def debug_etat_archives_foot() -> dict[str, Any]:
    """Diagnostic : totaux, EN_ATTENTE, prêts pour score, chemins."""
    archives = _charger_archives_foot()
    resultats = _lire_json(RESULTATS_PATH, {})
    now = datetime.now(tz=TZ_PARIS)
    en_attente: list[dict] = []
    prets: list[dict] = []
    sans_kickoff: list[dict] = []
    for a in archives:
        if not isinstance(a, dict):
            continue
        if _est_en_attente(a):
            en_attente.append(a)
            if _archive_pret_pour_score(a, now):
                prets.append(a)
            elif _kickoff_match(a) is None:
                sans_kickoff.append(a)
    info = {
        "fichier": str(ARCHIVES_FOOT_PATH.resolve()),
        "total": len(archives),
        "en_attente": len(en_attente),
        "pret_pour_score": len(prets),
        "sans_date_kickoff": len(sans_kickoff),
        "ids_pret": [str(a.get("id_match")) for a in prets],
        "equipes_pret": [
            f"{a.get('equipe_domicile')} — {a.get('equipe_exterieur')}"
            for a in prets
        ],
        "equipes_attente": [
            f"{a.get('equipe_domicile')} — {a.get('equipe_exterieur')} "
            f"(statut={a.get('statut')!r}, pari={a.get('statut_pari')!r})"
            for a in en_attente
        ],
    }
    print(f"[resolver-foot] Fichier archives : {info['fichier']}")
    print(f"[resolver-foot] Matchs dans le JSON : {info['total']}")
    print(f"[resolver-foot] EN_ATTENTE détectés : {info['en_attente']}")
    print(f"[resolver-foot] Prêts pour récupération score : {info['pret_pour_score']}")
    if info["sans_date_kickoff"]:
        print(
            f"[resolver-foot] Sans date/kickoff parseable : {info['sans_date_kickoff']} "
            "(ajoutez date_match ou match_start_ts)"
        )
    if info["equipes_attente"]:
        for ligne in info["equipes_attente"][:15]:
            print(f"  · en attente : {ligne}")
        if len(info["equipes_attente"]) > 15:
            print(f"  … et {len(info['equipes_attente']) - 15} autre(s)")
    if info["pret_pour_score"] and info["ids_pret"]:
        print(f"[resolver-foot] IDs à interroger : {', '.join(info['ids_pret'][:20])}")
    if info["en_attente"] and not info["pret_pour_score"]:
        print(
            "[resolver-foot] Les EN_ATTENTE ne sont pas encore « prêts » "
            "(match pas fini + 2h) ou date_match absente."
        )
    if info["total"] == 0:
        print(
            "[resolver-foot] Le JSON serveur est vide — les cartes « En attente » "
            "viennent peut‑être du localStorage du navigateur. Rechargez Foot puis "
            "relancez le pipeline, ou copiez le contenu de velora_archives_foot "
            "dans web/velora_archives_foot.json."
        )
    return info


def resoudre_matchs_en_attente(assurer_premium: bool = True) -> dict[str, Any]:
    """
    Cible les archives EN_ATTENTE : récupère scores, valide le pari, met à jour le JSON.
    """
    stats: dict[str, Any] = {
        "en_attente_avant": 0,
        "resolus": 0,
        "encore_attente": 0,
        "scores_recuperes": 0,
        "erreur": None,
    }
    now_paris = datetime.now(tz=TZ_PARIS)
    debug_etat_archives_foot()
    archives = _charger_archives_foot()

    snapshots = _lire_json(SNAPSHOT_PREMIUM_PATH, [])
    if not isinstance(snapshots, list):
        snapshots = []
    catalogue = _lire_json(MATCHS_JSON_PATH, [])
    if not isinstance(catalogue, list):
        catalogue = []

    par_id = _index_archives(archives)
    reinitialises = 0
    ids_a_purger: list[str] = []
    for mid, arch in list(par_id.items()):
        if _archive_doit_etre_revalidee(arch):
            par_id[mid] = _reinitialiser_archive_en_attente(arch)
            ids_a_purger.append(mid)
            reinitialises += 1
    if reinitialises:
        print(f"[resolver-foot] {reinitialises} archive(s) réinitialisée(s) pour re-validation")

    resultats = _lire_json(RESULTATS_PATH, {})
    if not isinstance(resultats, dict):
        resultats = {}
    if ids_a_purger:
        matchs_res = resultats.get("matchs")
        if isinstance(matchs_res, dict):
            for mid in ids_a_purger:
                matchs_res.pop(mid, None)
                matchs_res.pop(int(mid) if mid.isdigit() else mid, None)
        for mid in ids_a_purger:
            resultats.pop(mid, None)
        _ecrire_json(RESULTATS_PATH, resultats)

    if assurer_premium:
        _assurer_archives_coup_envoi(snapshots, par_id, now_paris, catalogue=catalogue)
        purges = _purger_archives_non_velora(par_id, snapshots)
        if purges:
            print(f"[resolver-foot] {purges} entrée(s) hors pronos Velora retirée(s).")

    en_attente = {k: v for k, v in par_id.items() if _est_en_attente(v)}
    stats["en_attente_avant"] = len(en_attente)

    if not isinstance(resultats, dict):
        resultats = {}

    ids_fetch: list[str] = []
    for mid, arch in en_attente.items():
        if not _archive_pret_pour_score(arch, now_paris):
            print(
                f"[resolver-foot] Skip fetch {mid} "
                f"({arch.get('equipe_domicile')} — {arch.get('equipe_exterieur')}) : "
                "match pas encore éligible (date/kickoff)"
            )
            continue
        if _lire_resultat_pour_match(resultats, mid) is None:
            ids_fetch.append(mid)

    print(f"[resolver-foot] IDs envoyés à Winamax/TheSportsDB : {len(ids_fetch)}")

    if ids_fetch:
        try:
            _, fusionner_fichier, _ = _importer_fetch_winamax()
            nouveaux, cascade_stats = _fetch_scores_cascade(ids_fetch, en_attente)
            stats["scores_recuperes"] = len(nouveaux)
            stats["cascade"] = cascade_stats
            if nouveaux:
                fusionner_fichier(RESULTATS_PATH, nouveaux)
                resultats = _lire_json(RESULTATS_PATH, {})
        except Exception as exc:
            stats["erreur"] = str(exc)
            print(f"[resolver-foot] Échec récupération scores : {exc}")

    resolus = 0
    for mid, arch in list(en_attente.items()):
        if not _archive_pret_pour_score(arch, now_paris):
            continue
        res = _lire_resultat_pour_match(resultats, mid)
        if res is None:
            continue
        match = _enrichir_match_pour_validation(arch, snapshots, catalogue)
        validee = valider_foot(match, res)
        if validee:
            par_id[mid] = validee
            resolus += 1

    stats["resolus"] = resolus
    stats["encore_attente"] = sum(1 for a in par_id.values() if _est_en_attente(a))

    archives_finales = sorted(
        par_id.values(),
        key=lambda a: a.get("match_start_ts") or a.get("timestamp") or 0,
        reverse=True,
    )
    _ecrire_json(ARCHIVES_FOOT_PATH, archives_finales)
    return stats


def fetch_winamax_results() -> dict[str, Any]:
    """
    Étape 1 du pipeline : aspire les scores finaux Winamax (PRELOADED_STATE / page match).
    Met à jour velora_foot_resultats.json → matchs[id] = { domicile, exterieur }.
    Les matchs live ou sans score final sont ignorés sans faire échouer le script.
    """
    stats: dict[str, Any] = {
        "demandes": 0,
        "recuperes": 0,
        "erreur": None,
        "ids_interroges": [],
    }

    archives = _lire_json(ARCHIVES_FOOT_PATH, [])
    if not isinstance(archives, list):
        archives = []
    snapshots = _lire_json(SNAPSHOT_PREMIUM_PATH, [])
    if not isinstance(snapshots, list):
        snapshots = []
    catalogue = _lire_json(MATCHS_JSON_PATH, [])
    if not isinstance(catalogue, list):
        catalogue = []

    resultats = _lire_json(RESULTATS_PATH, {})
    if not isinstance(resultats, dict):
        resultats = {}

    ids = _collecter_ids_matchs_sans_score(archives, snapshots, catalogue, resultats)
    stats["demandes"] = len(ids)
    stats["ids_interroges"] = ids

    if not ids:
        dbg = debug_etat_archives_foot()
        print(
            "[archiver-foot] Aucun match Foot en attente de score Winamax "
            f"(archives={dbg['total']}, EN_ATTENTE={dbg['en_attente']}, "
            f"prêts={dbg['pret_pour_score']})."
        )
        return stats

    print(f"[archiver-foot] Winamax : {len(ids)} match(s) à interroger…")
    try:
        fetch_scores, fusionner_fichier, _ = _importer_fetch_winamax()
        scores = fetch_scores(ids)
        stats["recuperes"] = len(scores)
        if scores:
            fusionner_fichier(RESULTATS_PATH, scores)
            print(
                f"[archiver-foot] {len(scores)}/{len(ids)} score(s) enregistré(s) "
                f"dans {RESULTATS_PATH.name} (clé matchs)"
            )
        else:
            print(
                f"[archiver-foot] 0/{len(ids)} score(s) final(aux) sur Winamax "
                "(live ou pas encore terminé — prochain run)."
            )
    except Exception as exc:
        stats["erreur"] = str(exc)
        print(f"[archiver-foot] Sync Winamax échouée (suite du pipeline) : {exc}")

    return stats


def traiter_archives_foot() -> dict[str, Any]:
    sync_stats = fetch_winamax_results()
    resolve_stats = resoudre_matchs_en_attente()

    archives = _lire_json(ARCHIVES_FOOT_PATH, [])
    if not isinstance(archives, list):
        archives = []

    now_paris = datetime.now(tz=TZ_PARIS)
    par_id = _index_archives(archives)

    resultats = _lire_json(RESULTATS_PATH, {})
    if not isinstance(resultats, dict):
        resultats = {}

    snapshots = _lire_json(SNAPSHOT_PREMIUM_PATH, [])
    if not isinstance(snapshots, list):
        snapshots = []
    catalogue = _lire_json(MATCHS_JSON_PATH, [])
    if not isinstance(catalogue, list):
        catalogue = []

    nouvelles = resolve_stats.get("resolus", 0)

    for match in snapshots:
        mid = str(match.get("id_match") or "")
        if not mid:
            continue
        if mid in par_id and _est_archive_terminee_validee(par_id[mid]):
            continue
        if not _match_deja_joue(match, now_paris):
            continue
        res = _lire_resultat_pour_match(resultats, mid)
        if res is None:
            continue
        archive = valider_foot(match, res)
        if archive and _est_archive_terminee_validee(archive):
            if mid not in par_id or not _est_archive_terminee_validee(par_id.get(mid) or {}):
                nouvelles += 1
            par_id[mid] = archive

    for mid, res in list((resultats.get("matchs") or {}).items()):
        key = str(mid)
        if key in par_id and _est_archive_terminee_validee(par_id[key]):
            continue
        if _lire_resultat_pour_match(resultats, key) is None:
            continue
        match = _match_record_pour_id(key, snapshots, catalogue, par_id.get(key))
        if not _match_deja_joue(match, now_paris):
            continue
        archive = valider_foot(match, res)
        if archive and _est_archive_terminee_validee(archive):
            par_id[key] = archive
            nouvelles += 1

    archives_finales = sorted(
        par_id.values(),
        key=lambda a: a.get("match_start_ts") or a.get("timestamp") or 0,
        reverse=True,
    )
    _ecrire_json(ARCHIVES_FOOT_PATH, archives_finales)

    from publish_communaute import construire_communaute

    validees = [a for a in archives_finales if _est_archive_terminee_validee(a)]
    communaute = construire_communaute(
        {
            "archives_foot": len(validees),
            "nouvelles_validations": nouvelles,
            "sync_winamax": sync_stats,
            "resolution_en_attente": resolve_stats,
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
    print("[archiver-foot] Étape B : résolution EN_ATTENTE (resolver)")
    print("[archiver-foot] Étape C : validation valider_foot() + communauté")
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
