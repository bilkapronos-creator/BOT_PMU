import requests
import re

# =====================================================================
# 1. LE CERVEAU DE L'ALGORITHME V2 (Notation affinée pour M_Tech)
# =====================================================================
def calculer_score_forme(musique_brute, discipline_du_jour):
    # Si le cheval n'a pas d'historique
    if not musique_brute or musique_brute in ["Inédit", "Non renseignée"]:
        return 0

    score_total = 0
    # On nettoie les années comme (25) ou (24)
    musique_propre = re.sub(r'\(\d{2}\)', '', musique_brute)
    # On sépare chaque course
    performances = [p for p in musique_propre.split() if p]

    # Barème de points selon la place à l'arrivée
    # 💡 MODIFICATION : 'D' passe à 0 pour ne pas écraser les chevaux rapides qui font des fautes
    bareme_places = {
        '1': 10, '2': 7, '3': 5, '4': 3, '5': 2, '6': 1, 
        '7': 0, '8': 0, '9': 0, '0': 0,
        'D': 0, 'T': -1, 'A': -1
    }

    for index, perf in enumerate(performances):
        if len(perf) < 2: 
            continue
            
        place = perf[0].upper()
        discipline_perf = perf[1].lower() # 'a' = attelé, 'm' = monté...
        
        # 1. Points de base pour la position
        points = bareme_places.get(place, 0)

        # 2. Pondération selon la fraîcheur (les plus récentes rapportent plus)
        if index == 0:
            multiplicateur_fraicheur = 1.5  # Course la plus récente
        elif index == 1:
            multiplicateur_fraicheur = 1.2  # Deuxième plus récente
        else:
            multiplicateur_fraicheur = 1.0  # Les anciennes

        # 3. Pondération selon la spécialité (Discipline)
        multiplicateur_discipline = 1.0 if discipline_perf == discipline_du_jour else 0.5

        # Calcul final pour cette performance
        score_total += (points * multiplicateur_fraicheur * multiplicateur_discipline)

    # 💡 MODIFICATION : Malus d'inexpérience
    # Si le cheval a moins de 3 courses, on divise son score par 2
    if len(performances) < 3:
        score_total = score_total * 0.5

    return round(score_total, 1)


# =====================================================================
# 2. LE SCRAPER (Connexion sécurisée)
# =====================================================================
url_pmu = "https://online.turfinfo.api.pmu.fr/rest/client/62/programme/23052026/R2/C2/participants?specialisation=OFFLINE"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

print("⚡ Connexion aux serveurs PMU et analyse de la course R2C2...")
response = requests.get(url_pmu, headers=headers)

if response.status_code == 200:
    data = response.json()
    participants = data.get("participants", [])
    
    tableau_pronostics = []
    discipline_course_du_jour = 'a' # Attelé
    
    def est_non_partant(c):
        if c.get("nonPartant") is True or c.get("estPartant") is False:
            return True
        statut = str(c.get("statut") or "").strip().upper().replace(" ", "_")
        if statut == "NON_PARTANT":
            return True
        inc = str(c.get("incident") or "").strip().upper().replace(" ", "_")
        return inc == "NON_PARTANT"

    # Analyse de chaque cheval un par un (NP exclus du classement)
    for cheval in participants:
        if est_non_partant(cheval):
            continue
        numero = cheval.get("numPmu")
        nom = cheval.get("nom")
        musique = cheval.get("musique", "Non renseignée")
        
        # Calcul du score algorithmique V2
        score_algorithme = calculer_score_forme(musique, discipline_course_du_jour)
        
        tableau_pronostics.append({
            "numero": numero,
            "nom": nom,
            "musique": musique,
            "score": score_algorithme
        })
    
    # =====================================================================
    # 3. LE CLASSEMENT (Tri du meilleur score au moins bon)
    # =====================================================================
    tableau
