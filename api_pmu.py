from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
import re

app = FastAPI(title="Velora Engine", description="Moteur d'analyse multi-sports")

# Configuration CORS pour autoriser les appels depuis ton site Vercel
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def calculer_score_forme(musique_brute, deferre_statut, discipline_du_jour='a'):
    if not musique_brute or musique_brute in ["Inédit", "Non renseignée"]:
        return 0
    score_total = 0
    # Nettoyage des chaînes de caractères (suppression des années entre parenthèses)
    musique_propre = re.sub(r'\(\d{2}\)', '', musique_brute)
    performances = [p for p in musique_propre.split() if p]
    bareme_places = {'1': 10, '2': 7, '3': 5, '4': 3, '5': 2, '6': 1, '7': 0, '8': 0, '9': 0, '0': 0, 'D': 0, 'T': -1, 'A': -1}

    for index, perf in enumerate(performances):
        if len(perf) < 2: continue
        place = perf[0].upper()
        discipline_perf = perf[1].lower() 
        points = bareme_places.get(place, 0)
        # Multiplicateurs basés sur l'historique et la discipline
        multiplicateur_fraicheur = 1.5 if index == 0 else (1.2 if index == 1 else 1.0)
        multiplicateur_discipline = 1.0 if discipline_perf == discipline_du_jour else 0.5
        score_total += (points * multiplicateur_fraicheur * multiplicateur_discipline)

    if len(performances) < 3:
        score_total = score_total * 0.5
        
    if deferre_statut == "TOUS":
        score_total += 2.0
        
    return round(score_total, 1)

@app.get("/analyser/{date}/{reunion}/{course}")
def analyser_course(date: str, reunion: str, course: str):
    # Nettoyage des paramètres d'entrée pour l'API PMU
    reunion_num = re.sub(r'\D', '', reunion)
    course_num = re.sub(r'\D', '', course)
    
    url_pmu = f"https://online.turfinfo.api.pmu.fr/rest/client/62/programme/{date}/{reunion_num}/{course_num}/participants?specialisation=OFFLINE"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    response = requests.get(url_pmu, headers=headers)
    if response.status_code != 200:
        return {"erreur": "Course introuvable ou non disponible"}
        
    data = response.json()
    participants = data.get("participants", [])
    tableau_pronostics = []
    
    for cheval in participants:
        score = calculer_score_forme(cheval.get("musique"), cheval.get("deferre"))
        tableau_pronostics.append({
            "numero": cheval.get("numPmu"),
            "nom": cheval.get("nom"),
            "score_mtech": score
        })
        
    # Tri des chevaux par score décroissant
    tableau_pronostics.sort(key=lambda x: x["score_mtech"], reverse=True)
    
    # Retourne la clé attendue par ton index.html
    return {"pronostic_officiel_mtech": tableau_pronostics}