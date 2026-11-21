#!/bin/sh
set -eu
cd "$(dirname "$0")"
chmod +x install_service.sh update.sh 2>/dev/null || true
python3 -m venv .venv
.venv/bin/python -m pip install .
mkdir -p data
[ -f secret.json ] || echo 'Hinweis: secret.json fehlt - fuer Telegram (und spaeter Echtgeld) anlegen, siehe README.'
sh ./install_service.sh
echo 'Installiert: titanbot.timer (alle 5 Minuten). Startet im Papiermodus (settings.json echtgeld.aktiv = false).'
echo 'Erster Lauf sofort: sudo systemctl start titanbot.service ; Protokoll: journalctl -u titanbot -n 40'
