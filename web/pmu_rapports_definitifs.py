"""
Rapports définitifs PMU (endpoint rapports-definitifs) → cote numérique pour le financier.
"""

from __future__ import annotations

import re
from typing import Any

from velora_resilience import ErreurReseauExterne, pmu_get

BASE_URL_PMU = "https://online.turfinfo.api.pmu.fr/rest/client/62/programme"
HEADERS_PMU = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


def normaliser_date_pmu(date: str) -> str:
    date_propre = re.sub(r"\D", "", date)
    if len(date_propre) != 8:
        return date_propre
    if date_propre[:2] in ("19", "20"):
        return f"{date_propre[6:8]}{date_propre[4:6]}{date_propre[:4]}"
    return date_propre


def normaliser_code_pmu(valeur: str, prefixe: str) -> str:
    numero = re.sub(r"\D", "", valeur)
    return f"{prefixe}{numero}" if numero else valeur.upper()


def _numeros_velora(archive: dict, n: int) -> list:
    source = archive.get("pronostic_velora") or archive.get("top3") or []
    numeros = []
    for cheval in source[:n]:
        if isinstance(cheval, dict) and cheval.get("is_non_partant"):
            continue
        try:
            numeros.append(int(cheval.get("numero")))
        except (TypeError, ValueError):
            continue
    return numeros

_RE_RAPPORT_TEXTE = re.compile(r"[\s\u00a0€$£]+", re.UNICODE)


def parse_rapport_pmu_valeur(valeur: Any) -> float | None:
    """
    Convertit un rapport PMU en float (multiplicateur pour 1 €, ex. 4.2).
    Accepte nombre, dict {rapport: x}, texte « 4,20 € », dividendes en centimes (> 50).
    """
    if valeur is None or valeur == "" or valeur == "-":
        return None

    if isinstance(valeur, dict):
        for cle in (
            "rapport",
            "dividendePourUnEuro",
            "dividende",
            "dividendePourUneMiseDeBase",
            "valeur",
        ):
            if cle in valeur and valeur[cle] is not None:
                parsed = parse_rapport_pmu_valeur(valeur[cle])
                if parsed is not None:
                    return parsed
        return None

    if isinstance(valeur, (int, float)):
        n = float(valeur)
    else:
        texte = _RE_RAPPORT_TEXTE.sub("", str(valeur).strip())
        texte = texte.replace(",", ".")
        if not texte:
            return None
        try:
            n = float(texte)
        except (TypeError, ValueError):
            return None

    # Dividendes API turfinfo : centimes pour 1 € (ex. 420 → 4,20 €)
    if n >= 50 and float(int(n)) == n:
        n = n / 100.0

    return round(n, 2) if n >= 1.01 else None


def _url_rapports_definitifs_pmu(date_pmu: str, reunion_pmu: str, course_pmu: str) -> str:
    return (
        f"{BASE_URL_PMU}/{date_pmu}/{reunion_pmu}/{course_pmu}/rapports-definitifs"
    )


def charger_rapports_definitifs_pmu(
    date_pmu: str, reunion_pmu: str, course_pmu: str,
) -> list[dict] | None:
    try:
        response = pmu_get(
            _url_rapports_definitifs_pmu(date_pmu, reunion_pmu, course_pmu),
            headers=HEADERS_PMU,
        )
    except ErreurReseauExterne:
        return None
    if response.status_code != 200:
        return None
    data = response.json()
    return data if isinstance(data, list) else None


def _index_rapports(blocs: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for bloc in blocs:
        if not isinstance(bloc, dict):
            continue
        code = str(bloc.get("typePari") or bloc.get("codePari") or "").strip().upper()
        if not code:
            continue
        lignes = bloc.get("rapports") or []
        if isinstance(lignes, list):
            out[code] = [r for r in lignes if isinstance(r, dict)]
    return out


def code_pari_rapport_depuis_archive(archive: dict) -> str | None:
    """Type de pari PMU pour l'endpoint rapports-definitifs."""
    label = str(archive.get("statut_pmu") or archive.get("type_pari_pmu") or "").strip()
    low = label.lower()
    if not label or "perdu" in low:
        return None
    if "simple" in low and "plac" in low:
        return "SIMPLE_PLACE"
    if "coupl" in low and "plac" in low:
        return "COUPLE_PLACE"
    if "coupl" in low:
        return "COUPLE_GAGNANT"
    if "trio" in low or "tierc" in low:
        return "TRIO"
    if "quart" in low and "bonus" not in low:
        return "QUARTE"
    if "quint" in low:
        return "QUINTE"
    if "super 4" in low or "super4" in low:
        return "SUPER_QUATRE"
    if "multi" in low:
        return "MULTI"
    if "plac" in low:
        return "SIMPLE_PLACE"
    if "gagnant" in low:
        return "SIMPLE_GAGNANT"
    return "SIMPLE_GAGNANT"


def _cheval_concerne_par_pari(archive: dict) -> int | None:
    """Numéro PMU concerné par un pari simple (gagnant / placé)."""
    arrivee = []
    for n in archive.get("arrivee_officielle") or []:
        try:
            arrivee.append(int(n))
        except (TypeError, ValueError):
            continue
    top3 = set(_numeros_velora(archive, 3))
    label = str(archive.get("statut_pmu") or "")

    if "Gagnant" in label and arrivee and arrivee[0] in top3:
        return arrivee[0]
    if "Placé" in label:
        for n in arrivee[1:3]:
            if n in top3:
                return n
        for n in arrivee:
            if n in top3:
                return n

    if archive.get("reussi_pmu"):
        try:
            return int(archive.get("pronosticNumero"))
        except (TypeError, ValueError):
            pass
        nums = _numeros_velora(archive, 1)
        return nums[0] if nums else None
    return None


def combinaison_rapport_depuis_archive(archive: dict, code_pari: str) -> str | None:
    """Combinaison attendue dans rapports-definitifs (ex. « 8 » ou « 8-5-4 »)."""
    code = code_pari.upper()
    if code in ("SIMPLE_GAGNANT", "SIMPLE_PLACE"):
        n = _cheval_concerne_par_pari(archive)
        return str(n) if n is not None else None

    arrivee = []
    for n in archive.get("arrivee_officielle") or []:
        try:
            arrivee.append(int(n))
        except (TypeError, ValueError):
            continue

    if code == "TRIO":
        sel = _numeros_velora(archive, 3)
        if len(sel) >= 3:
            return "-".join(str(x) for x in sel[:3])
        if len(arrivee) >= 3:
            return "-".join(str(x) for x in arrivee[:3])
        return None

    if code == "COUPLE_GAGNANT":
        sel = _numeros_velora(archive, 2)
        if len(sel) >= 2:
            return "-".join(str(x) for x in sel[:2])
        if len(arrivee) >= 2:
            return "-".join(str(x) for x in sorted(arrivee[:2]))
        return None

    if code == "COUPLE_PLACE":
        sel = _numeros_velora(archive, 2)
        if len(sel) >= 2:
            a, b = sel[0], sel[1]
            return f"{min(a, b)}-{max(a, b)}"
        return None

    if code in ("QUARTE", "QUINTE", "SUPER_QUATRE"):
        k = {"QUARTE": 4, "QUINTE": 5, "SUPER_QUATRE": 4}.get(code, 3)
        sel = _numeros_velora(archive, k)
        if len(sel) >= k:
            return "-".join(str(x) for x in sel[:k])
        if len(arrivee) >= k:
            return "-".join(str(x) for x in arrivee[:k])
    return None


def rapport_definitif_ligne(
    index: dict[str, list[dict]],
    code_pari: str,
    combinaison: str | None,
) -> float | None:
    if not combinaison:
        return None
    comb_norm = str(combinaison).strip().replace(" ", "")
    for ligne in index.get(code_pari.upper(), []):
        if str(ligne.get("combinaison") or "").strip().replace(" ", "") != comb_norm:
            continue
        for cle in ("dividendePourUnEuro", "rapport", "dividende"):
            rapport = parse_rapport_pmu_valeur(ligne.get(cle))
            if rapport is not None:
                return rapport
    return None


def injecter_rapport_definitif_archive(
    archive: dict,
    evaluation: dict | None = None,
    blocs_rapports: list[dict] | None = None,
) -> bool:
    """
    Renseigne cote_jouee / rapport_pmu (float) depuis l'API PMU.
    Met à jour la cote du cheval concerné dans pronostic_velora.
    """
    evaluation = evaluation or {}
    if archive.get("reussi_pmu") is False:
        archive["cote_jouee"] = None
        archive["rapport_pmu"] = None
        return False

    merged = {**archive, **{k: v for k, v in evaluation.items() if v is not None}}
    if merged.get("reussi_pmu") is not True:
        return False

    date_api = merged.get("dateApi") or merged.get("date_api")
    reunion = merged.get("reunion")
    course = merged.get("course")
    if not date_api or not reunion or not course:
        return False

    if blocs_rapports is None:
        date_pmu = normaliser_date_pmu(str(date_api))
        reunion_pmu = normaliser_code_pmu(str(reunion), "R")
        course_pmu = normaliser_code_pmu(str(course), "C")
        blocs_rapports = charger_rapports_definitifs_pmu(date_pmu, reunion_pmu, course_pmu)
    if not blocs_rapports:
        return False

    code_pari = code_pari_rapport_depuis_archive(merged)
    if not code_pari:
        return False

    combinaison = combinaison_rapport_depuis_archive(merged, code_pari)
    index = _index_rapports(blocs_rapports)
    rapport = rapport_definitif_ligne(index, code_pari, combinaison)

    # Pari simple : tenter l'autre type si combinaison non trouvée
    if rapport is None and code_pari == "SIMPLE_GAGNANT":
        rapport = rapport_definitif_ligne(index, "SIMPLE_PLACE", combinaison)
        if rapport is not None:
            code_pari = "SIMPLE_PLACE"
    elif rapport is None and code_pari == "SIMPLE_PLACE":
        rapport = rapport_definitif_ligne(index, "SIMPLE_GAGNANT", combinaison)
        if rapport is not None:
            code_pari = "SIMPLE_GAGNANT"

    if rapport is None:
        return False

    archive["rapport_pmu"] = float(rapport)
    archive["cote_jouee"] = float(rapport)
    archive["rapport_pmu_type"] = code_pari
    archive["rapport_pmu_combinaison"] = combinaison

    num = _cheval_concerne_par_pari(merged)
    if num is not None:
        for cheval in archive.get("pronostic_velora") or archive.get("top3") or []:
            if not isinstance(cheval, dict):
                continue
            if str(cheval.get("numero")) == str(num):
                cheval["cote"] = float(rapport)
                cheval["rapport_definitif"] = float(rapport)
                break
    return True
