#!/usr/bin/env bash
# Alerte optionnelle (Discord / Slack) si VELORA_ALERT_WEBHOOK_URL est défini.
set -euo pipefail

WEBHOOK="${VELORA_ALERT_WEBHOOK_URL:-}"
if [ -z "$WEBHOOK" ]; then
  echo "[alert] VELORA_ALERT_WEBHOOK_URL absent — notification GitHub uniquement."
  exit 0
fi

export ALERT_TITLE="${1:-Velora — alerte pipeline}"
export ALERT_BODY="${2:-Le workflow Velora a échoué. Consultez GitHub Actions.}"
export ALERT_RUN_URL="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-}/actions/runs/${GITHUB_RUN_ID:-}"

python - <<'PY'
import json
import os
import urllib.request

webhook = os.environ["VELORA_ALERT_WEBHOOK_URL"]
title = os.environ.get("ALERT_TITLE", "Velora")
body = os.environ.get("ALERT_BODY", "")
run_url = os.environ.get("ALERT_RUN_URL", "")
content = f"**{title}**\n{body}\n{run_url}".strip()
payload = json.dumps({"content": content}).encode("utf-8")
req = urllib.request.Request(
    webhook,
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=20):
    pass
print("[alert] Webhook envoyé.")
PY
