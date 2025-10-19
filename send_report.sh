#!/bin/bash

# --- Skript zum Senden von Dateien an Telegram ---
# Verwendung: bash send_report.sh <Dateiname>
# Beispiel:   bash send_report.sh optimal_portfolio_equity.csv

# Überprüfen, ob ein Dateiname übergeben wurde
if [ -z "$1" ]; then
    echo "Fehler: Du musst einen Dateinamen als Argument übergeben."
    echo "Beispiel: bash send_report.sh optimal_portfolio_equity.csv"
    exit 1
fi

FILENAME=$1
FILE_PATH="/root/jaegerbot/$FILENAME"

# Überprüfen, ob die Datei existiert
if [ ! -f "$FILE_PATH" ]; then
    echo "Fehler: Die Datei '$FILE_PATH' wurde nicht gefunden."
    exit 1
fi

echo "Lese API-Daten aus secret.json..."
BOT_TOKEN=$(cat secret.json | jq -r '.telegram.bot_token')
CHAT_ID=$(cat secret.json | jq -r '.telegram.chat_id')

# Eine passende Beschreibung erstellen
CAPTION="Backtest-Bericht für '$FILENAME' vom $(date)"

echo "Sende '$FILENAME' an Telegram..."

# Datei mit curl an die Telegram API senden
curl -s -X POST "https://api.telegram.org/bot$BOT_TOKEN/sendDocument" \
     -F "chat_id=$CHAT_ID" \
     -F "document=@$FILE_PATH" \
     -F "caption=$CAPTION" > /dev/null

echo "✔ Datei wurde erfolgreich an Telegram gesendet!"
