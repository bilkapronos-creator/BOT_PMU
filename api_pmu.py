import os
import re
from typing import Any, Callable, Optional, Tuple, TypeVar, Union

from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from database import init_db, lister_archives, obtenir_archive, sauvegarder_archive
from stats_pmu import get_stats_publiques, get_stats_utilisateur
from velora_resilience import ArchivesStorageError, ErreurReseauExterne, pmu_get

T = TypeVar("T")

load_dotenv()

app = FastAPI(title="Velora Engine", description="Moteur d'analyse multi-sports")


def _construire_origines_cors() -> tuple[list[str], bool]:
    """
    Origines autorisées pour le frontend (Vercel + dev local).
    Note : allow_origins=["*"] + allow_credentials=True est refusé par les navigateurs.
    """
    defaut = [
        "https://velora-engine.vercel.app",
        "http://localhost:5500",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://127.0.0.1:5500",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8080",
    ]
    extra = os.environ.get("CORS_ORIGINS", "")
    if extra.strip():
        defaut.extend(o.strip() for o in extra.split(",") if o.strip())
    # Dédupliquer en conservant l'ordre
    origines = list(dict.fromkeys(defaut))

    if os.environ.get("CORS_ALLOW_ALL", "").lower() in ("1", "true", "yes"):
        return ["*"], False

    return origines, True


_CORS_ORIGINS, _CORS_CREDENTIALS = _construire_origines_cors()

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=_CORS_CREDENTIALS,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-MTech-Key",
        "X-User-Id",
        "Accept",
        "Origin",
    ],
    expose_headers=["*"],
)


def _erreur_pmu_http(exc: ErreurReseauExterne) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail={
            "erreur": "API PMU temporairement indisponible. Réessayez dans quelques instants.",
            "message": str(exc)[:300],
        },
    )


def _erreur_archives_http(exc: ArchivesStorageError) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "erreur": "Stockage des archives temporairement indisponible.",
            "message": str(exc)[:300],
        },
    )


def _executer_archives(operation: Callable[[], T]) -> T:
    try:
        return operation()
    except ArchivesStorageError as exc:
        raise _erreur_archives_http(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.exception_handler(ArchivesStorageError)
async def handler_archives_storage(_request, exc: ArchivesStorageError):
    http_exc = _erreur_archives_http(exc)
    return JSONResponse(status_code=http_exc.status_code, content=http_exc.detail)


@app.exception_handler(ErreurReseauExterne)
async def handler_erreur_reseau_pmu(_request, exc: ErreurReseauExterne):
    http_exc = _erreur_pmu_http(exc)
    return JSONResponse(status_code=http_exc.status_code, content=http_exc.detail)


@app.on_event("startup")
def demarrer_application():
    from database import _resoudre_backend

    try:
        init_db()
    except ArchivesStorageError as err:
        print(f"[Velora] Supabase archives injoignable au démarrage : {err}")
    except RuntimeError as err:
        print(f"[Velora] Archives : erreur configuration — {err}")

    try:
        print(f"[Velora] Archives : backend « {_resoudre_backend()} »")
    except RuntimeError as err:
        print(f"[Velora] Archives : erreur configuration — {err}")


def verifier_cle_api(x_mtech_key: Optional[str] = Header(default=None, alias="X-MTech-Key")):
    cle_attendue = os.environ.get("MTECH_API_KEY")
    if not cle_attendue:
        raise HTTPException(status_code=500, detail="Clé API non configurée sur le serveur")
    if not x_mtech_key or x_mtech_key != cle_attendue:
        raise HTTPException(status_code=401, detail="Accès non autorisé : X-MTech-Key invalide")


def verifier_cle_api_publique(x_mtech_key: Optional[str] = Header(default=None, alias="X-MTech-Key")):
    """Clé dédiée vitrine (MTECH_PUBLIC_API_KEY) ou clé principale en secours."""
    cle_publique = os.environ.get("MTECH_PUBLIC_API_KEY") or os.environ.get("MTECH_API_KEY")
    if not cle_publique:
        raise HTTPException(status_code=500, detail="Clé API publique non configurée")
    if not x_mtech_key or x_mtech_key != cle_publique:
        raise HTTPException(status_code=401, detail="Accès non autorisé : clé API publique invalide")


def exiger_user_id(x_user_id: Optional[str] = Header(default=None, alias="X-User-Id")) -> str:
    if not x_user_id or not str(x_user_id).strip():
        raise HTTPException(status_code=400, detail="En-tête X-User-Id requis")
    return str(x_user_id).strip()


def auth_membre(
    _cle: None = Depends(verifier_cle_api),
    user_id: str = Depends(exiger_user_id),
) -> str:
    """Exige X-MTech-Key + X-User-Id sur les routes membres."""
    return user_id


def valider_user_id_archive(archive: dict, user_id: str) -> None:
    """Le user_id du corps doit correspondre à l'utilisateur authentifié."""
    archive_uid = archive.get("user_id")
    if archive_uid and str(archive_uid).strip() != user_id:
        raise HTTPException(
            status_code=403,
            detail="user_id du corps ne correspond pas à X-User-Id",
        )
    if "user_id" in archive and not archive_uid:
        raise HTTPException(status_code=400, detail="user_id invalide dans le corps")


def construire_archive_complete(user_id: str, archive: dict, evaluation: dict, meta: dict) -> dict:
    """Assemble l'objet archive persisté (évaluation PMU inchangée + user_id)."""
    gagnant = evaluation.get("gagnant")
    if gagnant is None and evaluation.get("arrivee_officielle"):
        gagnant = evaluation["arrivee_officielle"][0]

    return {
        **archive,
        "user_id": user_id,
        "nombre_partants": meta.get("nombre_partants") or archive.get("nombre_partants"),
        "est_quinte": meta.get("est_quinte", archive.get("est_quinte", False)),
        "statut": "Terminée" if evaluation.get("terminee") else "En attente",
        "terminee": bool(evaluation.get("terminee")),
        "gagnant": gagnant,
        "resultat": f"Gagnant N°{gagnant}" if gagnant is not None else "-",
        "arrivee_officielle": evaluation.get("arrivee_officielle") or [],
        "reussi_pmu": evaluation.get("reussi_pmu"),
        "type_pari_pmu": evaluation.get("type_pari_pmu"),
        "statut_pmu": evaluation.get("statut_pmu"),
        "mode_pari_pmu": evaluation.get("mode_pari_pmu"),
        "badges_pmu": evaluation.get("badges_pmu") or [],
        "resultats_pmu_detectes": evaluation.get("resultats_pmu_detectes") or [],
    }


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


COTE_INDICATOR = "-"
SEUIL_SCORE_BELLE_COTE = 7.0
SEUIL_COTE_BELLE_COTE = 8.0
TOP_CLASSEMENT_BELLE_COTE = 4
ECART_MIN_VALUE_BET = 1.0  # cote PMU doit dépasser le score Velora d'au moins ce delta

BASE_URL_PMU = "https://online.turfinfo.api.pmu.fr/rest/client/62/programme"

HEADERS_PMU = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


def _extraire_rapport_nombre(valeur: Any) -> Optional[float]:
    """Extrait un rapport PMU depuis un nombre ou un objet {rapport: x}."""
    if valeur is None or valeur == "" or valeur == "-":
        return None

    if isinstance(valeur, dict):
        valeur = valeur.get("rapport")

    if valeur is None or valeur == "" or valeur == "-":
        return None

    try:
        cote = float(str(valeur).replace(",", "."))
    except (TypeError, ValueError):
        return None

    return round(cote, 1) if cote > 0 else None


def extraire_cote_pmu(cheval: dict) -> Tuple[Optional[float], bool]:
    """Retourne (cote, cote_pmu_disponible). Priorité au direct live, puis référence."""
    for cle in (
        "dernierRapportDirect",
        "dernierRapportDirectInternational",
        "rapportDirect",
        "dernierRapportReference",
        "dernierRapportRef",
        "rapportProbable",
        "cote",
    ):
        cote = _extraire_rapport_nombre(cheval.get(cle))
        if cote is not None:
            return cote, True
    return None, False


def _url_participants_pmu(date_pmu: str, reunion_pmu: str, course_pmu: str, specialisation: str) -> str:
    return (
        f"{BASE_URL_PMU}/{date_pmu}/{reunion_pmu}/{course_pmu}/participants"
        f"?specialisation={specialisation}"
    )


def _fetch_participants_pmu(
    date_pmu: str, reunion_pmu: str, course_pmu: str, specialisation: str = "OFFLINE"
) -> list:
    try:
        response = pmu_get(
            _url_participants_pmu(date_pmu, reunion_pmu, course_pmu, specialisation),
            headers=HEADERS_PMU,
        )
    except ErreurReseauExterne:
        return []
    if response.status_code != 200:
        return []
    return response.json().get("participants", [])


def _enrichir_cotes_internet(participants: list, date_pmu: str, reunion_pmu: str, course_pmu: str) -> list:
    """Complète les cotes manquantes via l'endpoint INTERNET (cotes live web)."""
    sans_cote = [p for p in participants if not extraire_cote_pmu(p)[1]]
    if not sans_cote:
        return participants

    participants_internet = _fetch_participants_pmu(date_pmu, reunion_pmu, course_pmu, "INTERNET")
    if not participants_internet:
        return participants

    cotes_par_num = {p.get("numPmu"): p for p in participants_internet if p.get("numPmu") is not None}
    for cheval in participants:
        if extraire_cote_pmu(cheval)[1]:
            continue
        source = cotes_par_num.get(cheval.get("numPmu"))
        if not source:
            continue
        for cle in ("dernierRapportDirect", "dernierRapportReference"):
            if source.get(cle) and not cheval.get(cle):
                cheval[cle] = source[cle]
    return participants


def _formater_cote_reponse(cote: Optional[float], disponible: bool) -> Union[float, str]:
    return cote if disponible and cote is not None else COTE_INDICATOR


def est_belle_cote(
    score_mtech: float,
    cote: Optional[float],
    rang: int,
    cote_pmu_disponible: bool,
) -> bool:
    """
    Value Bet / Belle Côte : top 4 Velora, score solide, cote PMU live nettement
    supérieure au score (anomalie de sous-estimation du marché).
    """
    if rang >= TOP_CLASSEMENT_BELLE_COTE or score_mtech < SEUIL_SCORE_BELLE_COTE:
        return False
    if not cote_pmu_disponible or cote is None:
        return False
    return cote >= SEUIL_COTE_BELLE_COTE and cote >= score_mtech + ECART_MIN_VALUE_BET


def _numeros_velora(archive: dict, n: int) -> list:
    """Numéros PMU du Top N Velora (ordre de classement conservé)."""
    source = archive.get("pronostic_velora") or archive.get("top3") or []
    numeros = []
    for cheval in source[:n]:
        try:
            numeros.append(int(cheval.get("numero")))
        except (TypeError, ValueError):
            continue
    return numeros


def _ordre_exact(arrivee: list, selection: list, k: int) -> bool:
    if len(arrivee) < k or len(selection) < k:
        return False
    return arrivee[:k] == selection[:k]


def _desordre(arrivee: list, selection: list, k: int) -> bool:
    if len(arrivee) < k or not selection:
        return False
    pool = set(selection)
    return all(n in pool for n in arrivee[:k])


def _premiers_arrivee_dans_selection(arrivee: list, selection: list, nb: int) -> bool:
    """True si les nb premiers chevaux de l'arrivée sont tous dans selection (Top N Velora)."""
    if len(arrivee) < nb or not selection:
        return False
    pool = set(selection)
    return all(n in pool for n in arrivee[:nb])


def _quarte_bonus_3(arrivee: list, top4: list) -> bool:
    """3 des 4 premiers dans le Top 4 Velora, sans les 4."""
    if len(arrivee) < 4 or len(top4) < 4:
        return False
    pool = set(top4)
    matches = sum(1 for n in arrivee[:4] if n in pool)
    return matches == 3


def _ajouter_resultat(resultats: list, priorite: int, label: str, style: str, mode: str) -> None:
    resultats.append({
        "priorite": priorite,
        "label": label,
        "type_pari_pmu": label,
        "style": style,
        "mode": mode,
    })


def _fabriquer_badge(label: str, style: str, mode: str, priorite: int = 1) -> dict:
    return {
        "priorite": priorite,
        "label": label,
        "type_pari_pmu": label,
        "style": style,
        "mode": mode,
    }


def _badge_perdu() -> dict:
    return _fabriquer_badge("Perdu", "red", None, priorite=999)


def _evaluer_multi_super4(
    arrivee: list,
    top4: list,
    top5: list,
    top6: list,
    top7: list,
    partants: int,
) -> Optional[dict]:
    """
    Bloc 1 : les 4 premiers de l'arrivée dans le Top 4/5/6/7 Velora.
    Du plus restrictif (Top 4) au plus large (Top 7), puis libellé selon partants.
    """
    if len(arrivee) < 4:
        return None

    if _premiers_arrivee_dans_selection(arrivee, top4, 4):
        if partants <= 9:
            return _fabriquer_badge("Super 4", "violet", "groupe", priorite=10)
        if 10 <= partants <= 13:
            return _fabriquer_badge("Mini Multi en 4", "violet", "groupe", priorite=11)
        if partants >= 14:
            return _fabriquer_badge("Multi en 4", "violet", "groupe", priorite=12)

    if partants >= 10 and _premiers_arrivee_dans_selection(arrivee, top5, 4):
        if 10 <= partants <= 13:
            return _fabriquer_badge("Mini Multi en 5", "violet", "groupe", priorite=13)
        if partants >= 14:
            return _fabriquer_badge("Multi en 5", "violet", "groupe", priorite=14)

    if partants >= 10 and _premiers_arrivee_dans_selection(arrivee, top6, 4):
        if 10 <= partants <= 13:
            return _fabriquer_badge("Mini Multi en 6", "violet", "groupe", priorite=15)
        if partants >= 14:
            return _fabriquer_badge("Multi en 6", "violet", "groupe", priorite=16)

    if partants >= 14 and _premiers_arrivee_dans_selection(arrivee, top7, 4):
        return _fabriquer_badge("Multi en 7", "violet", "groupe", priorite=17)

    return None


def _evaluer_badge_principal(
    arrivee: list,
    top3: list,
    top4: list,
    top5: list,
    top6: list,
    top7: list,
    partants: int,
    est_quinte: bool,
) -> dict:
    """
    Hiérarchie stricte (un seul badge) : Multi/Super4 → Trio/Tiercé → Gagnant → Placé → Perdu.
    Courses Quinté+ : Quinté et Quarté avant le bloc Multi.
    """
    if est_quinte:
        if _ordre_exact(arrivee, top5, 5):
            return _fabriquer_badge("Quinté Ordre", "gold", "ordre", priorite=1)
        if _desordre(arrivee, top5, 5):
            return _fabriquer_badge("Quinté Désordre", "gold", "desordre", priorite=2)
        if _ordre_exact(arrivee, top4, 4):
            return _fabriquer_badge("Quarté Ordre", "gold", "ordre", priorite=3)
        if _desordre(arrivee, top4, 4):
            return _fabriquer_badge("Quarté Désordre", "gold", "desordre", priorite=4)
        if _quarte_bonus_3(arrivee, top4):
            return _fabriquer_badge("Quarté Bonus 3", "gold", "bonus", priorite=5)

    badge_multi = _evaluer_multi_super4(arrivee, top4, top5, top6, top7, partants)
    if badge_multi:
        return badge_multi

    if _premiers_arrivee_dans_selection(arrivee, top3, 3):
        if est_quinte:
            if _ordre_exact(arrivee, top3, 3):
                return _fabriquer_badge("Tiercé Ordre", "gold", "ordre", priorite=20)
            return _fabriquer_badge("Tiercé Désordre", "gold", "desordre", priorite=21)
        if _ordre_exact(arrivee, top3, 3):
            return _fabriquer_badge("Trio Ordre", "gold", "ordre", priorite=20)
        return _fabriquer_badge("Trio Désordre", "gold", "desordre", priorite=21)

    if len(arrivee) >= 1 and arrivee[0] in set(top3):
        return _fabriquer_badge("Gagnant", "green", "simple", priorite=30)

    if len(arrivee) >= 2 and (
        arrivee[1] in set(top3) or (len(arrivee) >= 3 and arrivee[2] in set(top3))
    ):
        return _fabriquer_badge("Placé", "orange", "simple", priorite=40)

    return _badge_perdu()


def evaluer_pronostic_pmu(archive: dict, arrivee_officielle: list) -> dict:
    """
    Évalue le pronostic Velora vs l'arrivée officielle (terminologie PMU).
    Un seul badge affiché : du pari le plus difficile au plus simple.
    """
    arrivee = []
    for n in arrivee_officielle or []:
        try:
            arrivee.append(int(n))
        except (TypeError, ValueError):
            continue

    if len(arrivee) < 1:
        perdu = _badge_perdu()
        return {
            "reussi_pmu": False,
            "type_pari_pmu": "Perdu",
            "statut_pmu": "Perdu",
            "mode_pari_pmu": None,
            "badges_pmu": [perdu],
            "resultats_pmu_detectes": [],
        }

    partants = int(
        archive.get("nombre_partants")
        or len(archive.get("pronostic_velora") or archive.get("top3") or [])
        or 0
    )
    est_quinte = bool(archive.get("est_quinte"))

    top3 = _numeros_velora(archive, 3)
    top4 = _numeros_velora(archive, 4)
    top5 = _numeros_velora(archive, 5)
    top6 = _numeros_velora(archive, 6)
    top7 = _numeros_velora(archive, 7)

    meilleur = _evaluer_badge_principal(
        arrivee, top3, top4, top5, top6, top7, partants, est_quinte
    )

    if meilleur["label"] == "Perdu":
        return {
            "reussi_pmu": False,
            "type_pari_pmu": "Perdu",
            "statut_pmu": "Perdu",
            "mode_pari_pmu": None,
            "badges_pmu": [meilleur],
            "resultats_pmu_detectes": [],
        }

    return {
        "reussi_pmu": True,
        "type_pari_pmu": meilleur["type_pari_pmu"],
        "statut_pmu": meilleur["label"],
        "mode_pari_pmu": meilleur["mode"],
        "badges_pmu": [meilleur],
        "resultats_pmu_detectes": [meilleur],
    }


def _metadata_course_pmu(date_pmu: str, reunion_pmu: str, course_pmu: str, nb_participants: int = 0) -> dict:
    """Retourne nombre de partants et indicateur Quinté depuis l'API PMU."""
    try:
        response = pmu_get(
            _url_course_pmu(date_pmu, reunion_pmu, course_pmu),
            headers=HEADERS_PMU,
        )
    except ErreurReseauExterne:
        return {
            "nombre_partants": nb_participants,
            "est_quinte": False,
        }
    if response.status_code != 200:
        return {
            "nombre_partants": nb_participants,
            "est_quinte": False,
        }

    data = response.json()
    paris = data.get("paris") or []
    est_quinte = any(
        "QUINT" in str(p.get("codePari") or p.get("typePari", "")).upper()
        for p in paris
        if isinstance(p, dict)
    )
    nombre_partants = data.get("nombreDeclaresPartants") or nb_participants
    return {
        "nombre_partants": int(nombre_partants) if nombre_partants else nb_participants,
        "est_quinte": est_quinte,
    }


@app.get("/analyser/{date}/{reunion}/{course}", dependencies=[Depends(verifier_cle_api)])
def analyser_course(date: str, reunion: str, course: str):
    date_pmu = normaliser_date_pmu(date)
    reunion_pmu = normaliser_code_pmu(reunion, "R")
    course_pmu = normaliser_code_pmu(course, "C")

    url_pmu = _url_participants_pmu(date_pmu, reunion_pmu, course_pmu, "OFFLINE")

    try:
        response = pmu_get(url_pmu, headers=HEADERS_PMU)
    except ErreurReseauExterne as exc:
        raise _erreur_pmu_http(exc) from exc
    if response.status_code != 200:
        return {"erreur": "Course introuvable ou non disponible"}

    participants = response.json().get("participants", [])
    participants = _enrichir_cotes_internet(participants, date_pmu, reunion_pmu, course_pmu)
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
            "cote": _formater_cote_reponse(cote, cote_pmu_disponible),
            "cote_pmu_disponible": cote_pmu_disponible,
            "score_mtech": score,
            "score_velora": score,
            "is_value_bet": False,
            "anomalie_cote": None,
        })

    tableau_pronostics.sort(key=lambda x: x["score_mtech"], reverse=True)

    for rang, entree in enumerate(tableau_pronostics):
        cote_num = entree["cote"] if isinstance(entree["cote"], (int, float)) else None
        score = float(entree["score_mtech"] or 0)
        is_vb = est_belle_cote(
            score,
            cote_num,
            rang,
            entree["cote_pmu_disponible"],
        )
        entree["is_value_bet"] = is_vb
        if is_vb and cote_num is not None:
            entree["anomalie_cote"] = round(cote_num - score, 1)

    return {
        "pronostic_officiel_mtech": tableau_pronostics,
        **_metadata_course_pmu(date_pmu, reunion_pmu, course_pmu, len(participants)),
    }


def _url_course_pmu(date_pmu: str, reunion_pmu: str, course_pmu: str) -> str:
    return f"{BASE_URL_PMU}/{date_pmu}/{reunion_pmu}/{course_pmu}"


@app.get("/resultat/{date}/{reunion}/{course}", dependencies=[Depends(verifier_cle_api)])
def resultat_course(date: str, reunion: str, course: str):
    date_pmu = normaliser_date_pmu(date)
    reunion_pmu = normaliser_code_pmu(reunion, "R")
    course_pmu = normaliser_code_pmu(course, "C")

    try:
        response = pmu_get(
            _url_course_pmu(date_pmu, reunion_pmu, course_pmu),
            headers=HEADERS_PMU,
        )
    except ErreurReseauExterne as exc:
        raise _erreur_pmu_http(exc) from exc
    if response.status_code != 200:
        return {"erreur": "Course introuvable ou non disponible"}

    data = response.json()
    ordre_brut = data.get("ordreArrivee") or []
    ordre_arrivee = [arrivee[0] for arrivee in ordre_brut if arrivee]
    arrivee_officielle = ordre_arrivee[:5]
    meta = _metadata_course_pmu(date_pmu, reunion_pmu, course_pmu)

    return {
        "terminee": len(arrivee_officielle) > 0,
        "gagnant": arrivee_officielle[0] if arrivee_officielle else None,
        "arrivee_officielle": arrivee_officielle,
        "ordre_arrivee": arrivee_officielle,
        **meta,
    }


@app.post("/evaluation")
def evaluation_archive(
    archive: dict = Body(...),
    user_id: str = Depends(auth_membre),
):
    """
    Évalue un pronostic archivé (logique evaluer_pronostic_pmu inchangée),
    persiste l'archive avec user_id et renvoie l'objet complet.
    """
    valider_user_id_archive(archive, user_id)
    archive["user_id"] = user_id

    date_api = archive.get("dateApi") or archive.get("date_api")
    reunion = archive.get("reunion")
    course = archive.get("course")
    if not date_api or not reunion or not course:
        raise HTTPException(
            status_code=400,
            detail="Archive incomplète (dateApi, reunion, course requis)",
        )

    date_pmu = normaliser_date_pmu(str(date_api))
    reunion_pmu = normaliser_code_pmu(str(reunion), "R")
    course_pmu = normaliser_code_pmu(str(course), "C")

    try:
        response = pmu_get(
            _url_course_pmu(date_pmu, reunion_pmu, course_pmu),
            headers=HEADERS_PMU,
        )
    except ErreurReseauExterne as exc:
        raise _erreur_pmu_http(exc) from exc
    if response.status_code != 200:
        return {"erreur": "Course introuvable ou non disponible"}

    data = response.json()
    ordre_brut = data.get("ordreArrivee") or []
    ordre_arrivee = [arrivee[0] for arrivee in ordre_brut if arrivee]
    arrivee_officielle = ordre_arrivee[:5]
    meta = _metadata_course_pmu(date_pmu, reunion_pmu, course_pmu)

    if len(arrivee_officielle) == 0:
        archive_en_attente = {
            **archive,
            **meta,
            "statut": "En attente",
            "terminee": False,
            "reussi_pmu": None,
            "type_pari_pmu": None,
            "statut_pmu": None,
            "mode_pari_pmu": None,
            "badges_pmu": [],
            "resultats_pmu_detectes": [],
            "arrivee_officielle": [],
        }
        archive_sauvee = _executer_archives(
            lambda: sauvegarder_archive(user_id, archive_en_attente),
        )
        return {"archive": archive_sauvee, **archive_sauvee}

    archive_enrichie = {
        **archive,
        "nombre_partants": meta.get("nombre_partants") or archive.get("nombre_partants"),
        "est_quinte": meta.get("est_quinte", archive.get("est_quinte", False)),
    }
    evaluation_pmu = evaluer_pronostic_pmu(archive_enrichie, arrivee_officielle)
    evaluation_pmu["terminee"] = True
    evaluation_pmu["arrivee_officielle"] = arrivee_officielle
    evaluation_pmu["gagnant"] = arrivee_officielle[0]

    archive_complete = construire_archive_complete(
        user_id, archive_enrichie, evaluation_pmu, meta
    )
    archive_sauvee = _executer_archives(
        lambda: sauvegarder_archive(user_id, archive_complete),
    )
    return {"archive": archive_sauvee, **archive_sauvee}


@app.get("/archives")
def get_archives_utilisateur(
    user_id: str = Depends(auth_membre),
    limit: int = Query(default=10, ge=1, le=50),
    user_id_query: Optional[str] = Query(default=None, alias="user_id"),
):
    """Archives privées : uniquement celles du X-User-Id authentifié."""
    if user_id_query and user_id_query.strip() != user_id:
        raise HTTPException(
            status_code=403,
            detail="user_id en paramètre ne correspond pas à X-User-Id",
        )
    archives = _executer_archives(lambda: lister_archives(user_id, limit=limit))
    return {"user_id": user_id, "archives": archives, "total": len(archives)}


@app.post("/archives")
def creer_archive(
    archive: dict = Body(...),
    user_id: str = Depends(auth_membre),
):
    """Enregistre une analyse sans réévaluation (course en attente de résultat)."""
    valider_user_id_archive(archive, user_id)
    archive["user_id"] = user_id
    if not archive.get("statut"):
        archive["statut"] = "En attente"
    archive_sauvee = _executer_archives(lambda: sauvegarder_archive(user_id, archive))
    return {"archive": archive_sauvee}


@app.get("/archives/{archive_id}")
def get_archive_par_id(
    archive_id: int,
    user_id: str = Depends(auth_membre),
):
    """Détail d'une archive — refusée si elle n'appartient pas au membre."""
    archive = _executer_archives(lambda: obtenir_archive(user_id, archive_id))
    if not archive:
        raise HTTPException(status_code=404, detail="Archive introuvable")
    return {"archive": archive}


@app.get("/stats")
def get_stats_membre(
    user_id: str = Depends(auth_membre),
    user_id_query: Optional[str] = Query(default=None, alias="user_id"),
):
    """Statistiques personnalisées : réussites par type de pari (hors Perdu)."""
    if user_id_query and user_id_query.strip() != user_id:
        raise HTTPException(
            status_code=403,
            detail="user_id en paramètre ne correspond pas à X-User-Id",
        )
    return _executer_archives(lambda: get_stats_utilisateur(user_id))


@app.get("/stats/publiques", dependencies=[Depends(verifier_cle_api_publique)])
def get_stats_vitrine():
    """Agrégats anonymisés plateforme (aucune donnée par utilisateur)."""
    return _executer_archives(get_stats_publiques)


# Alias : logique de calcul historique (inchangée)
evaluerPronosticVelora = evaluer_pronostic_pmu
