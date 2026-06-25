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
echo "  4) Interaktive Charts (SMC mit Backtest-Simulation + Equity Curve)"

# Input-Validierung: nur 1-4 akzeptieren
while true; do
    read -p "Auswahl (1-4) [Standard: 1]: " MODE
    MODE=${MODE:-1}
    # Prüfe ob Eingabe nur aus einer Ziffer 1-4 besteht
    if [[ "$MODE" =~ ^[1-4]$ ]]; then
        break
    else
        echo -e "${RED}Ungültige Eingabe. Bitte nur 1, 2, 3 oder 4 eingeben.${NC}"
    fi
done

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

# --- Datum & Kapital (gilt für Modus 1, 2, 3) ---
TODAY=$(date +%Y-%m-%d)
if [ "$MODE" != "4" ]; then
    read -p "Startdatum (JJJJ-MM-TT) [Standard: 2023-01-01]: " START_DATE
    START_DATE=${START_DATE:-2023-01-01}
    read -p "Enddatum (JJJJ-MM-TT) [Standard: ${TODAY}]: " END_DATE
    END_DATE=${END_DATE:-$TODAY}
    read -p "Startkapital in USDT [Standard: 1000]: " START_CAPITAL
    START_CAPITAL=${START_CAPITAL:-1000}
fi

if [ ! -f "$RESULTS_SCRIPT" ]; then
    echo -e "${RED}Fehler: Die Analyse-Datei '$RESULTS_SCRIPT' wurde nicht gefunden.${NC}"
    deactivate
    exit 1
fi

# *** NEU: Übergebe alle Parameter an das Python Skript ***
if [ "$MODE" == "4" ]; then
    python3 "$RESULTS_SCRIPT" --mode "$MODE"
else
    python3 "$RESULTS_SCRIPT" --mode "$MODE" --target_max_drawdown "$TARGET_MAX_DD" \
        --start_date "$START_DATE" --end_date "$END_DATE" --start_capital "$START_CAPITAL"
fi

deactivate
