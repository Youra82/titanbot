#!/bin/bash
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
VENV_PATH=".venv/bin/activate"
RESULTS_SCRIPT="src/titanbot/analysis/show_results.py"

source "$VENV_PATH"

# --- MODUS-MENÜ ---
echo -e "\n${YELLOW}Wähle einen Analyse-Modus:${NC}"
echo "  1) Einzel-Analyse (jede Strategie wird isoliert getestet)"
echo "  2) Manuelle Portfolio-Simulation (du wählst das Team)"
echo "  3) Automatische Portfolio-Optimierung (der Bot wählt das beste Team)"
read -p "Auswahl (1-3) [Standard: 1]: " MODE
MODE=${MODE:-1}

# *** NEU: Max Drawdown Abfrage für Modus 3 ***
TARGET_MAX_DD=30 # Standardwert
if [ "$MODE" == "3" ]; then
    read -p "Gewünschter maximaler Drawdown in % für die Optimierung [Standard: 30]: " DD_INPUT
    # Prüfe, ob eine gültige Zahl eingegeben wurde, sonst nimm Standard
    if [[ "$DD_INPUT" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
        TARGET_MAX_DD=$DD_INPUT
    else
        echo "Ungültige Eingabe, verwende Standard: ${TARGET_MAX_DD}%"
    fi
fi
# *** ENDE NEU ***

if [ ! -f "$RESULTS_SCRIPT" ]; then
    echo -e "${RED}Fehler: Die Analyse-Datei '$RESULTS_SCRIPT' wurde nicht gefunden.${NC}"
    deactivate
    exit 1
fi

# *** NEU: Übergebe Max DD an das Python Skript ***
python3 "$RESULTS_SCRIPT" --mode "$MODE" --target_max_drawdown "$TARGET_MAX_DD"

deactivate
