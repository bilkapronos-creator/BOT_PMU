#!/usr/bin/env bash
# Push des JSON générés par run_all — sans merge sur un arbre de travail sale.
set -euo pipefail

JSON_FILES=(
  api_velora_matchs.json
  api_velora_matchs_tennis.json
  api_velora_premium.json
  dump_winamax_html.json
  web/api_velora_matchs.json
  web/api_velora_matchs_tennis.json
  web/api_velora_premium.json
  web/api_velora_communaute.json
  web/velora_archives_foot.json
  web/velora_foot_resultats.json
  web/velora_odds_history.json
  web/velora_foot_calibration.json
  web/velora_pronos_history.json
)

MSG="${1:-ci: mise à jour JSON Velora (matchs, premium, archives)}"

git fetch origin main

# Sauvegarde hors dépôt (reset --hard ne les touche pas)
BACKUP="$(mktemp -d)"
for f in "${JSON_FILES[@]}"; do
  if [ -f "$f" ]; then
    mkdir -p "$BACKUP/$(dirname "$f")"
    cp -f "$f" "$BACKUP/$f"
  fi
done

found=0
for f in "${JSON_FILES[@]}"; do
  if [ -f "$BACKUP/$f" ]; then
    found=1
    break
  fi
done
if [ "$found" -eq 0 ]; then
  echo "Aucun fichier JSON trouvé après le pipeline."
  exit 1
fi

# Repartir de origin/main puis réappliquer uniquement les JSON du run
git reset --hard origin/main
for f in "${JSON_FILES[@]}"; do
  if [ -f "$BACKUP/$f" ]; then
    mkdir -p "$(dirname "$f")"
    cp -f "$BACKUP/$f" "$f"
    git add -f "$f"
  fi
done
rm -rf "$BACKUP"

if git diff --staged --quiet; then
  echo "JSON inchangés par rapport à origin/main — pas de push."
  exit 0
fi

git commit -m "$MSG"

for attempt in 1 2 3; do
  if git push origin HEAD:main; then
    echo "Push réussi (tentative ${attempt})."
    exit 0
  fi
  echo "Push refusé (tentative ${attempt}/3) — sync origin/main…"
  git fetch origin main
  git rebase origin/main || git rebase --abort || true
  sleep $((attempt * 2))
done

echo "::error::git push échoué après 3 tentatives."
exit 1
