#!/bin/bash
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
cd "$SCRIPT_DIR"

CONFIGS_DIR="src/titanbot/strategy/configs"

echo ""
echo -e "${YELLOW}========== CONFIGS PUSHEN ==========${NC}"
echo ""

# Pruefe ob Config-Dateien existieren
CONFIG_COUNT=$(ls "$CONFIGS_DIR"/config_*.json 2>/dev/null | wc -l)
if [ "$CONFIG_COUNT" -eq 0 ]; then
    echo -e "${RED}Keine Konfigurationsdateien gefunden in: $CONFIGS_DIR${NC}"
    echo -e "${YELLOW}Bitte zuerst run_pipeline.sh ausfuehren.${NC}"
    exit 1
fi

echo "Gefundene Konfigurationen ($CONFIG_COUNT):"
for f in "$CONFIGS_DIR"/config_*.json; do
    echo "  - $(basename "$f")"
done
echo ""

# Aenderungen pruefen
git add "$CONFIGS_DIR"/config_*.json settings.json push_configs.sh
STAGED=$(git diff --cached --name-only)

if [ -z "$STAGED" ]; then
    echo -e "${YELLOW}Keine Aenderungen — Configs sind bereits aktuell im Repo.${NC}"
    exit 0
fi

echo "Geaenderte Dateien:"
echo "$STAGED" | sed 's/^/  /'
echo ""

# Erst Remote-Stand holen (rebase) bevor wir committen — so gibt es nur einen Push
echo -e "${YELLOW}Hole Remote-Stand...${NC}"
git stash
git pull origin main --rebase
if [ $? -ne 0 ]; then
    git stash pop 2>/dev/null
    echo -e "${RED}Pull/Rebase fehlgeschlagen. Bitte manuell loesen.${NC}"
    exit 1
fi
git stash pop 2>/dev/null

# Erneut stagen (nach stash pop koennen die Dateien dirty sein)
git add "$CONFIGS_DIR"/config_*.json settings.json push_configs.sh

# Commit
TIMESTAMP=$(date '+%Y-%m-%d %H:%M')
git commit -m "Update: titanbot Konfigurationen aktualisiert ($TIMESTAMP)"

# Einmal pushen — kein Konflikt mehr moeglich
echo ""
echo -e "${YELLOW}Pushe auf origin/main...${NC}"
git push origin HEAD:main

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}Configs erfolgreich gepusht!${NC}"
else
    echo -e "${RED}Push fehlgeschlagen.${NC}"
    exit 1
fi
