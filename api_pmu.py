import os
import re
from typing import Optional, Tuple

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

app = FastAPI(title="Velora Engine", description="Moteur d'analyse multi-sports")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def verifier_cle_api(x_mtech_key: Optional[str] = Header(default=None, alias="X-MTech-Key")):
    cle_attendue = os.environ.get("MTECH_API_KEY")
    if not cle_attendue:
        raise HTTPException(status_code=500, detail="Clé API non configurée sur le serveur")
    if not x_mtech_key or x_mtech_key != cle_attendue:
        raise HTTPException(status_code=401, detail="Accès non autorisé")


def calculer_score_forme(musique_brute, deferre_statut, discipline_du_jour='a'):
    if not musique_brute or musique_brute in ["Inédit", "Non renseignée"]:
        return 0
    score_total = 0
    musique_propre = re.sub(r'\(\d{2}\)', '', musique_brute)
    performances = [p for p in musique_propre.split() if p]
    bareme_places = {
        '1': 10, '2': 7, '3': 5, '4': 3, '5': 2, '6': 1,
        '7': 0, '8': 0, '9': 0, '0': 0, 'D': 0, 'T': -1, 'A': -1,
    }

    for index, perf in enumerate(performances):
        if len(perf) < 2:
            continue
        place = perf[0].upper()
        discipline_perf = perf[1].lower()
        points = bareme_places.get(place, 0)
        multiplicateur_fraicheur = 1.5 if index == 0 else (1.2 if index == 1 else 1.0)
        multiplicateur_discipline = 1.0 if discipline_perf == discipline_du_jour else 0.5
        score_total += points * multiplicateur_fraicheur * multiplicateur_discipline

    if len(performances) < 3:
        score_total = score_total * 0.5

    if deferre_statut == "TOUS":
        score_total += 2.0

    return round(score_total, 1)


def normaliser_date_pmu(date: str) -> str:
    """Convertit YYYY-MM-DD ou YYYYMMDD vers DDMMYYYY (format attendu par l'API PMU)."""
    date_propre = re.sub(r'\D', '', date)
    if len(date_propre) != 8:
        return date_propre
    if date_propre[:2] in ("19", "20"):
        return f"{date_propre[6:8]}{date_propre[4:6]}{date_propre[:4]}"
    return date_propre


def normaliser_code_pmu(valeur: str, prefixe: str) -> str:
    """Normalise R1/C3 : accepte '1', 'R1' ou 'r1' et renvoie 'R1'."""
    numero = re.sub(r'\D', '', valeur)
    return f"{prefixe}{numero}" if numero else valeur.upper()


COTE_DEFAUT = 10.0
SEUIL_SCORE_BELLE_COTE = 7.0
SEUIL_COTE_BELLE_COTE = 8.0
TOP_CLASSEMENT_BELLE_COTE = 4


def extraire_cote_pmu(cheval: dict) -> Tuple[float, bool]:
    """Retourne (cote, cote_pmu_disponible)."""
    for cle in ("cote", "rapportDirect", "dernierRapportDirect", "rapportProbable", "dernierRapportRef"):
        val = cheval.get(cle)
        if val in (None, "", "-"):
            continue
        try:
            cote = float(str(val).replace(",", "."))
            if cote > 0:
                return round(cote, 1), True
        except ValueError:
            continue
    return COTE_DEFAUT, False


def est_belle_cote(score_mtech: float, cote: float, rang: int, cote_pmu_disponible: bool) -> bool:
    if rang >= TOP_CLASSEMENT_BELLE_COTE or score_mtech < SEUIL_SCORE_BELLE_COTE:
        return False
    if cote_pmu_disponible:
        return cote >= SEUIL_COTE_BELLE_COTE
    return score_mtech >= SEUIL_SCORE_BELLE_COTE + 3


@app.get("/analyser/{date}/{reunion}/{course}", dependencies=[Depends(verifier_cle_api)])
def analyser_course(date: str, reunion: str, course: str):
    date_pmu = normaliser_date_pmu(date)
    reunion_pmu = normaliser_code_pmu(reunion, "R")
    course_pmu = normaliser_code_pmu(course, "C")

    url_pmu = (
        f"https://online.turfinfo.api.pmu.fr/rest/client/62/programme/"
        f"{date_pmu}/{reunion_pmu}/{course_pmu}/participants?specialisation=OFFLINE"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    response = requests.get(url_pmu, headers=headers, timeout=20)
    if response.status_code != 200:
        return {"erreur": "Course introuvable ou non disponible"}

    data = response.json()
    participants = data.get("participants", [])
    tableau_pronostics = []

    for cheval in participants:
        score = calculer_score_forme(cheval.get("musique"), cheval.get("deferre"))

        jockey_info = cheval.get("jockey") or {}
        jockey_nom = jockey_info.get("nom") if isinstance(jockey_info, dict) else None
        if not jockey_nom:
            driver = cheval.get("driver")
            jockey_nom = driver.get("nom") if isinstance(driver, dict) else driver

        poids = cheval.get("poids")
        if poids is None:
            poids = cheval.get("handicapPoids")

        cote, cote_pmu_disponible = extraire_cote_pmu(cheval)

        tableau_pronostics.append({
            "numero": cheval.get("numPmu"),
            "nom": cheval.get("nom"),
            "jockey": jockey_nom,
            "poids": poids,
            "cote": cote,
            "cote_pmu_disponible": cote_pmu_disponible,
            "score_mtech": score,
            "is_value_bet": False,
        })

    tableau_pronostics.sort(key=lambda x: x["score_mtech"], reverse=True)

    for rang, entree in enumerate(tableau_pronostics):
        entree["is_value_bet"] = est_belle_cote(
            entree["score_mtech"],
            entree["cote"],
            rang,
            entree["cote_pmu_disponible"],
        )

    return {"pronostic_officiel_mtech": tableau_pronostics}
