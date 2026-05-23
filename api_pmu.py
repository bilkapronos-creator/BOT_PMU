from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
import re

app = FastAPI(title="API Pronostics M_Tech", description="Moteur d'analyse de courses PMU")

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
    musique_propre = re.sub(r'\(\d{2}\)', '', musique_brute)
    performances = [p for p in musique_propre.split() if p]
    bareme_places = {'1': 10, '2': 7, '3': 5, '4': 3, '5': 2, '6': 1, '7': 0, '8': 0, '9': 0, '0': 0, 'D': 0, 'T': -1, 'A': -1}

    for index, perf in enumerate(performances):
        if len(perf) < 2: continue
        place = perf[0].upper()
        discipline_perf = perf[1].lower() 
        points = bareme_places.get(place, 0)
        multiplicateur_fraicheur = 1.5 if index == 0 else (1.2 if index == 1 else 1.0)
        multiplicateur_discipline = 1.0 if discipline_perf == discipline_du_jour else 0.5
        score_total += (points * multiplicateur_fraicheur * multiplicateur_discipline)

    if len(performances) < 3:
        score_total = score_total * 0.5
        
    # Bonus Déferré des 4 (+2 pts)
    if deferre_statut == "TOUS":
        score_total += 2.0
        
    return round(score_total, 1)

@app.get("/analyser/{date}/{reunion}/{course}")
def analyser_course(date: str, reunion: str, course: str):
    url_pmu = f"https://online.turfinfo.api.pmu.fr/rest/client/62/programme/{date}/{reunion}/{course}/participants?specialisation=OFFLINE"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    response = requests.get(url_pmu, headers=headers)
    if response.status_code != 200:
        return {"erreur": f"Course introuvable ou erreur réseau ({response.status_code})"}
        
    data = response.json()
    participants = data.get("participants", [])
    tableau_pronostics = []
    
    for cheval in participants:
        numero = cheval.get("numPmu")
        nom = cheval.get("nom")
        musique = cheval.get("musique", "Non renseignée")
        deferre = cheval.get("deferre", "NON")
        driver = cheval.get("driver", "Non renseigné")
        
        score = calculer_score_forme(musique, deferre)
        
        cote = "-"
        if "dernierRapportDirect" in cheval and cheval["dernierRapportDirect"]:
            cote = cheval["dernierRapportDirect"].get("rapport", "-")
        elif "dernierRapportReference" in cheval and cheval["dernierRapportReference"]:
            cote = cheval["dernierRapportReference"].get("rapport", "-")
        
        tableau_pronostics.append({
            "numero": numero,
            "nom": nom,
            "musique": musique,
            "driver": driver,
            "deferre": deferre,
            "score_mtech": score,
            "cote": cote
        })
        
    tableau_pronostics.sort(key=lambda x: x["score_mtech"], reverse=True)
    
    return {
        "course": f"{reunion}{course}",
        "date": date,
        "partants_analyses": len(tableau_pronostics),
        "pronostic_officiel_mtech": tableau_pronostics
    }

### 2. L'Application : PWA (`index.html` + `manifest.json` + `sw.js`)

#### A. Le nouveau `index.html` (avec Driver et PWA)
Ajoute ce bloc dans le `<head>` pour lier la PWA :
```html
<link rel="manifest" href="manifest.json">
<script>
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js');
  }
</script>
Et mets à jour la partie affichage du cheval pour inclure le **Driver** et le bonus **D4**.

#### B. Crée un fichier `manifest.json`
```json
{
  "name": "M_Tech Pronostics",
  "short_name": "M_Tech",
  "start_url": "index.html",
  "display": "standalone",
  "background_color": "#111827",
  "theme_color": "#10B981",
  "icons": [
    { "src": "icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}

### 3. Le Lancement : Hébergement
1. **Frontend :** Dépose ton `index.html`, `manifest.json` et `sw.js` sur **Vercel** ou **Netlify** (c'est gratuit et super rapide).
2. **Backend :** Dépose ton `api_pmu.py` sur **Render.com**.
3. **Lien :** Une fois ton API en ligne, change l'adresse `http://127.0.0.1:8000` dans ton JavaScript par l'adresse que Render va te donner (ex: `https://m-tech-api.onrender.com`).

Félicitations ! Ton projet **M_Tech** est officiellement prêt à conquérir les hippodromes. On s'attaque à la mise en ligne effective ?
