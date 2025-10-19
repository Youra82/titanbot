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
# *** KORRIGIERTER PFAD: Nimmt den Pfad des Skripts als Basis ***
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
FILE_PATH="$SCRIPT_DIR/$FILENAME"

# Überprüfen, ob die Datei existiert
if [ ! -f "$FILE_PATH" ]; then
    echo "Fehler: Die Datei '$FILE_PATH' wurde nicht gefunden."
    exit 1
fi

# Prüfen ob jq installiert ist
if ! command -v jq &> /dev/null
then
    echo "Fehler: 'jq' ist nicht installiert. Bitte installieren (sudo apt-get install jq)."
    exit 1
fi

echo "Lese API-Daten aus secret.json..."
# Stelle sicher, dass secret.json im selben Verzeichnis wie das Skript ist
SECRET_JSON_PATH="$SCRIPT_DIR/secret.json"
if [ ! -f "$SECRET_JSON_PATH" ]; then
    echo "Fehler: secret.json nicht im Skriptverzeichnis gefunden ($SCRIPT_DIR)."
    exit 1
fi

BOT_TOKEN=$(cat "$SECRET_JSON_PATH" | jq -r '.telegram.bot_token // empty')
CHAT_ID=$(cat "$SECRET_JSON_PATH" | jq -r '.telegram.chat_id // empty')

if [ -z "$BOT_TOKEN" ] || [ "$BOT_TOKEN" == "null" ]; then
    echo "Fehler: 'bot_token' nicht in secret.json gefunden oder leer."
    exit 1
fi
if [ -z "$CHAT_ID" ] || [ "$CHAT_ID" == "null" ]; then
    echo "Fehler: 'chat_id' nicht in secret.json gefunden oder leer."
    exit 1
fi


# Eine passende Beschreibung erstellen
CAPTION="Backtest-Bericht für '$FILENAME' vom $(date)"

echo "Sende '$FILENAME' an Telegram..."

# Datei mit curl an die Telegram API senden
curl -s -X POST "https://api.telegram.org/bot$BOT_TOKEN/sendDocument" \
     -F "chat_id=$CHAT_ID" \
     -F "document=@$FILE_PATH" \
     -F "caption=$CAPTION" > /dev/null

# Prüfe den Exit-Code von curl
if [ $? -eq 0 ]; then
    echo "✔ Datei wurde erfolgreich an Telegram gesendet!"
else
    echo "❌ Fehler beim Senden an Telegram via curl."
fi
