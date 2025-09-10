#!/bin/bash

# Pfad zum Projektverzeichnis dynamisch ermitteln
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
VENV_PATH="$SCRIPT_DIR/code/.venv/bin/activate"

# Alle relevanten Skripte und temporären Dateien
GLOBAL_OPTIMIZER="$SCRIPT_DIR/code/analysis/global_optimizer_pymoo.py"
LOCAL_REFINER="$SCRIPT_DIR/code/analysis/local_refiner_optuna.py"
BACKTESTER="$SCRIPT_DIR/code/analysis/run_backtest.py"
CANDIDATES_FILE="optimization_candidates.json"
CHECKPOINT_FILE="pymoo_checkpoint.pkl"
INPUTS_FILE="optim_inputs.json"
OPTUNA_DB="optuna_studies.db"
CACHE_DIR="$SCRIPT_DIR/code/analysis/historical_data"

# Farbcodes für die Ausgabe
GREEN='\033[0;32m'; BLUE='\033[0;34m'; RED='\033[0;31m'; NC='\033[0m'

# Virtuelle Umgebung aktivieren
if [ -f "$VENV_PATH" ]; then source "$VENV_PATH"; else
    echo -e "${RED}Fehler: Virtuelle Umgebung nicht gefunden. Bitte 'install.sh' ausführen.${NC}"; exit 1; fi

# Hauptmenü
echo -e "${BLUE}======================================================="
echo "        TitanBot Analyse- & Optimierungs-Werkzeuge"
echo -e "=======================================================${NC}"
echo "Wähle einen Modus:"
echo "  1) Komplette Optimierungs-Pipeline starten"
echo "  2) Einzel-Backtest der aktuellen Live-Konfiguration starten"
echo "  3) Daten-Cache löschen"
read -p "Auswahl (1-3): " mode

case "$mode" in
    1)
        echo -e "\n${GREEN}>>> Modus: Optimierungs-Pipeline für TitanBot gewählt.${NC}"
        
        RESUME_FLAG=""
        # Prüfe, ob ein Checkpoint von Stufe 1 existiert
        if [ -f "$CHECKPOINT_FILE" ]; then
            read -p "Eine unterbrochene Optimierung wurde gefunden. Fortsetzen? [J/n]: " response
            if [[ "$response" =~ ^([jJ][aA]|[jJ]|)$ ]]; then
                RESUME_FLAG="--resume"
                echo "Setze Optimierung fort..."
            else
                # Benutzer will NICHT fortsetzen -> ALLES löschen
                echo "Checkpoint wird ignoriert. Starte neue Optimierung und lösche ALLE alten Daten..."
                rm -f "$CHECKPOINT_FILE" "$INPUTS_FILE" "$CANDIDATES_FILE" "$OPTUNA_DB"
            fi
        else
            # Kein Stufe-1-Checkpoint gefunden -> Es ist ein neuer Start. Alte Ergebnisdateien löschen.
            echo "Starte neue Optimierung. Lösche eventuelle alte Ergebnisdateien..."
            rm -f "$CANDIDATES_FILE" "$OPTUNA_DB"
        fi
        
        # CPU-Kerne nur abfragen, wenn es ein neuer Start ist
        if [ -z "$RESUME_FLAG" ]; then
             read -p "Mit wie vielen CPU-Kernen soll optimiert werden? (Standard: 1): " N_CORES; N_CORES=${N_CORES:-1}
        else
             # Beim Fortsetzen einen Standardwert annehmen, da die Eingaben schon gespeichert sind
             N_CORES=2
        fi
        
        echo -e "${GREEN}>>> STARTE STUFE 1: Globale Suche mit Pymoo...${NC}"
        python3 "$GLOBAL_OPTIMIZER" --jobs "$N_CORES" $RESUME_FLAG
        
        # Prüfe, ob Stufe 1 erfolgreich war (via Exit Code)
        if [ $? -ne 0 ]; then
            echo -e "${RED}Fehler: Stufe 1 hat keine Ergebnisse geliefert. Breche ab.${NC}"
            deactivate
            exit 1
        fi

        echo -e "\n${GREEN}>>> STARTE STUFE 2: Lokale Verfeinerung mit Optuna...${NC}"
        python3 "$LOCAL_REFINER" --jobs "$N_CORES"
        
        # Am Ende einer ERFOLGREICHEN Pipeline werden die Checkpoint-Dateien aufgeräumt
        rm -f "$CHECKPOINT_FILE" "$INPUTS_FILE"
        ;;
    2)
        echo -e "\n${GREEN}>>> Modus: Einzel-Backtest gewählt.${NC}"
        python3 "$BACKTESTER"
        ;;
    3)
        echo -e "\n${GREEN}>>> Modus: Cache löschen gewählt.${NC}"
        read -p "Möchtest du den gesamten Daten-Cache wirklich löschen? [j/N]: " response
        if [[ "$response" =~ ^([jJ][aA]|[jJ])$ ]]; then rm -rfv "$CACHE_DIR"/*
            echo -e "${GREEN}✔ Cache wurde erfolgreich gelöscht.${NC}"; else echo -e "${RED}Aktion abgebrochen.${NC}"; fi
        ;;
    *)
        echo -e "${RED}Ungültige Auswahl. Skript wird beendet.${NC}"
        ;;
esac

deactivate
echo -e "\n${BLUE}Aktion abgeschlossen.${NC}"
