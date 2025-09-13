#!/bin/bash

# Pfad zum Projektverzeichnis dynamisch ermitteln
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
VENV_PATH="$SCRIPT_DIR/code/.venv/bin/activate"
GLOBAL_OPTIMIZER="$SCRIPT_DIR/code/analysis/global_optimizer_pymoo.py"
LOCAL_REFINER="$SCRIPT_DIR/code/analysis/local_refiner_optuna.py"
BACKTESTER="$SCRIPT_DIR/code/analysis/run_backtest.py"
CANDIDATES_FILE="$SCRIPT_DIR/code/analysis/optimization_candidates.json"
STATE_FILE_PIPELINE="$SCRIPT_DIR/code/analysis/pipeline_state.json" # Für Fortsetzung
STATE_FILE_PYMOO="$SCRIPT_DIR/code/analysis/pymoo_checkpoint.pkl" # Für Fortsetzung
STATE_FILE_OPTUNA_DB="$SCRIPT_DIR/code/analysis/optuna_studies.db" # Für Fortsetzung
INPUTS_FILE="$SCRIPT_DIR/code/analysis/optim_inputs.json" # Für Fortsetzung

# --- Farbcodes ---
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# --- Virtuelle Umgebung aktivieren ---
if [ -f "$VENV_PATH" ]; then
    source "$VENV_PATH"
else
    echo -e "${RED}Fehler: Virtuelle Umgebung nicht gefunden. Bitte 'install.sh' ausführen.${NC}"
    exit 1
fi

# --- Hauptmenü ---
echo -e "${BLUE}======================================================="
echo "         TitanBot Analyse- & Optimierungs-Werkzeuge"
echo -e "=======================================================${NC}"
echo "Wähle einen Modus:"
echo "  1) Komplette Optimierungs-Pipeline starten"
echo "  2) Eine unterbrochene Optimierung fortsetzen"
echo "  3) Einzel-Backtest der aktuellen Live-Konfiguration starten"
echo "  4) Daten-Cache löschen"
read -p "Auswahl (1-4): " mode

case "$mode" in
    1)
        echo -e "\n${GREEN}>>> Modus: Optimierungs-Pipeline für TitanBot gewählt.${NC}"
        echo -e "${YELLOW}Starte neue Optimierung. Lösche eventuelle alte Ergebnisdateien...${NC}"
        rm -f "$CANDIDATES_FILE" "$STATE_FILE_PIPELINE" "$STATE_FILE_PYMOO" "$STATE_FILE_OPTUNA_DB" "$INPUTS_FILE"

        read -p "Mit wie vielen CPU-Kernen soll optimiert werden? (Standard: 1): " N_CORES
        N_CORES=${N_CORES:-1}

        echo -e "${GREEN}>>> STARTE STUFE 1: Globale Suche mit Pymoo...${NC}"
        python3 "$GLOBAL_OPTIMIZER" --jobs "$N_CORES"

        if [ ! -f "$CANDIDATES_FILE" ]; then
            echo -e "${RED}Fehler: Stufe 1 hat keine Ergebnisse geliefert. Breche ab.${NC}"
            deactivate
            exit 1
        fi

        echo -e "\n${GREEN}>>> STARTE STUFE 2: Lokale Verfeinerung mit Optuna...${NC}"
        # --- KORREKTUR: Feste Kern-Anzahl für Optuna, um DB-Lock zu vermeiden ---
        echo -e "${YELLOW}Hinweis: Stufe 2 wird mit maximal 4 Kernen ausgeführt, um Datenbank-Locks zu vermeiden.${NC}"
        OPTUNA_CORES=$((N_CORES > 4 ? 4 : N_CORES))
        python3 "$LOCAL_REFINER" --jobs "$OPTUNA_CORES"
        ;;
    2)
        echo -e "\n${GREEN}>>> Modus: Optimierung fortsetzen gewählt.${NC}"
        read -p "Mit wie vielen CPU-Kernen soll die Optimierung fortgesetzt werden? (Standard: 1): " N_CORES
        N_CORES=${N_CORES:-1}
        
        if [ -f "$CANDIDATES_FILE" ]; then
             echo -e "${YELLOW}Stufe 1 scheint abgeschlossen. Setze direkt bei Stufe 2 fort...${NC}"
             echo -e "${YELLOW}Hinweis: Stufe 2 wird mit maximal 4 Kernen ausgeführt, um Datenbank-Locks zu vermeiden.${NC}"
             OPTUNA_CORES=$((N_CORES > 4 ? 4 : N_CORES))
             python3 "$LOCAL_REFINER" --jobs "$OPTUNA_CORES" --trials 200
        else
             echo -e "${YELLOW}Setze Stufe 1 (Pymoo) fort...${NC}"
             python3 "$GLOBAL_OPTIMIZER" --jobs "$N_CORES" --resume
             
             if [ ! -f "$CANDIDATES_FILE" ]; then
                echo -e "${RED}Fehler: Stufe 1 hat auch nach Fortsetzung keine Ergebnisse geliefert. Breche ab.${NC}"
                deactivate
                exit 1
             fi
             
             echo -e "\n${GREEN}>>> STARTE STUFE 2: Lokale Verfeinerung mit Optuna...${NC}"
             echo -e "${YELLOW}Hinweis: Stufe 2 wird mit maximal 4 Kernen ausgeführt, um Datenbank-Locks zu vermeiden.${NC}"
             OPTUNA_CORES=$((N_CORES > 4 ? 4 : N_CORES))
             python3 "$LOCAL_REFINER" --jobs "$OPTUNA_CORES"
        fi
        ;;
    3)
        echo -e "\n${GREEN}>>> Modus: Einzel-Backtest gewählt.${NC}"
        python3 "$BACKTESTER"
        ;;
    4)
        echo -e "\n${GREEN}>>> Modus: Cache löschen gewählt.${NC}"
        read -p "Möchtest du den gesamten Daten-Cache wirklich löschen? [j/N]: " response
        if [[ "$response" =~ ^([jJ][aA]|[jJ])$ ]]; then
            rm -rfv "$SCRIPT_DIR/code/analysis/historical_data"/*
            echo -e "${GREEN}✔ Cache wurde erfolgreich gelöscht.${NC}"
        else
            echo -e "${RED}Aktion abgebrochen.${NC}"
        fi
        ;;
    *)
        echo -e "${RED}Ungültige Auswahl. Skript wird beendet.${NC}"
        ;;
esac

deactivate
echo -e "\n${BLUE}Aktion abgeschlossen.${NC}"
