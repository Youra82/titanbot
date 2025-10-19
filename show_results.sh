#!/bin/bash
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'
VENV_PATH=".venv/bin/activate"
# *** KORRIGIERTER PFAD ***
RESULTS_SCRIPT="src/titanbot/analysis/show_results.py" 
# *** ENDE KORREKTUR ***

source "$VENV_PATH"

# --- ERWEITERTES MODUS-MENÜ ---
echo -e "\n${YELLOW}Wähle einen Analyse-Modus:${NC}"
echo "  1) Einzel-Analyse (jede Strategie wird isoliert getestet)"
echo "  2) Manuelle Portfolio-Simulation (du wählst das Team)"
echo "  3) Automatische Portfolio-Optimierung (der Bot wählt das beste Team)"
read -p "Auswahl (1-3) [Standard: 1]: " MODE
MODE=${MODE:-1}

# Stelle sicher, dass die Python-Datei existiert, bevor sie aufgerufen wird
if [ ! -f "$RESULTS_SCRIPT" ]; then
    echo -e "${RED}Fehler: Die Analyse-Datei '$RESULTS_SCRIPT' wurde nicht gefunden.${NC}"
    deactivate
    exit 1
fi

python3 "$RESULTS_SCRIPT" --mode "$MODE"

deactivate
