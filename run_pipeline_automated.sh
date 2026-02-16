#!/bin/bash

# --- Pfade und Skripte ---
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
VENV_PATH="$SCRIPT_DIR/.venv/bin/activate"
SETTINGS_FILE="$SCRIPT_DIR/settings.json"
# *** Korrigierte Pfade, Trainer entfernt ***
OPTIMIZER="src/titanbot/analysis/optimizer.py"
CACHE_DIR="$SCRIPT_DIR/data/cache"
TIMESTAMP_FILE="$CACHE_DIR/.last_cleaned"

# --- Umgebung aktivieren ---
# Sicherstellen, dass die venv existiert
if [ ! -f "$VENV_PATH" ]; then
    echo "Fehler: Virtuelle Umgebung nicht gefunden unter $VENV_PATH. Bitte install.sh ausführen."
    exit 1
fi
source "$VENV_PATH"

echo "--- Starte automatischen Pipeline-Lauf (TitanBot SMC) ---"

# --- Prüfen ob settings.json existiert ---
if [ ! -f "$SETTINGS_FILE" ]; then
    echo "Fehler: settings.json nicht gefunden."
    deactivate
    exit 1
fi

# --- Python-Helper zum sicheren Auslesen der JSON-Datei ---
# Stellt sicher, dass die Datei existiert und Python verfügbar ist
get_setting() {
    python3 -c "import json, sys; f=open('$SETTINGS_FILE'); settings=json.load(f); keys=$1; current=settings; path_ok=True; for k in keys: current=current.get(k); if current is None: path_ok=False; break; print(current if path_ok else ''); f.close()" 2>/dev/null
}

# --- Standardwerte für Einstellungen definieren ---
DEFAULT_LOOKBACK=365
DEFAULT_START_CAPITAL=1000
DEFAULT_CORES=-1
DEFAULT_TRIALS=200
DEFAULT_MAX_DD=30
DEFAULT_MIN_WR=55
DEFAULT_MIN_PNL=0
DEFAULT_OPTIM_MODE="strict"
DEFAULT_CACHE_DAYS=0

# --- Automatisches Cache-Management ---
CACHE_DAYS=$(get_setting "['optimization_settings', 'auto_clear_cache_days']")
CACHE_DAYS=${CACHE_DAYS:-$DEFAULT_CACHE_DAYS} # Nutze Default, wenn leer oder Fehler

if [[ "$CACHE_DAYS" =~ ^[0-9]+$ ]] && [ "$CACHE_DAYS" -gt 0 ]; then
    mkdir -p "$CACHE_DIR"
    if [ ! -f "$TIMESTAMP_FILE" ]; then touch "$TIMESTAMP_FILE"; fi
    # Prüfe, ob die Datei älter als N-1 Tage ist (mtime +N-1)
    if find "$TIMESTAMP_FILE" -mtime +$((CACHE_DAYS - 1)) -print -quit | grep -q .; then
        echo "Cache ist älter als $CACHE_DAYS Tage. Leere den Cache..."
        rm -rf "$CACHE_DIR"/*
        touch "$TIMESTAMP_FILE" # Zeitstempel aktualisieren
    else
        echo "Cache ist aktuell. Keine Reinigung notwendig."
    fi
else
    echo "Automatisches Cache-Management deaktiviert oder ungültiger Wert ($CACHE_DAYS)."
fi


# --- Lese Pipeline-Einstellungen ---
# Verwende Standardwerte, falls Schlüssel nicht existieren
# Robustly read optimization_settings.enabled (prefer jq, fallback to python)
if command -v jq &> /dev/null; then
    ENABLED=$(jq -r '.optimization_settings.enabled // false' "$SETTINGS_FILE")
else
    ENABLED=$(python3 -c "import json;print(json.load(open('$SETTINGS_FILE')).get('optimization_settings',{}).get('enabled', False))")
fi
ENABLED=${ENABLED:-false} # Default ist false
ENABLED_LC=$(echo "$ENABLED" | tr '[:upper:]' '[:lower:]')

if [ "$ENABLED_LC" != "true" ]; then
    echo "Automatische Optimierung ist in settings.json deaktiviert. Breche ab."
    deactivate
    exit 0
fi

# Extrahiere Arrays sicher mit jq, falls verfügbar, sonst Fallback
if command -v jq &> /dev/null; then
    SYMBOLS=$(jq -r '.optimization_settings.symbols_to_optimize | join(" ") // ""' "$SETTINGS_FILE")
    TIMEFRAMES=$(jq -r '.optimization_settings.timeframes_to_optimize | join(" ") // ""' "$SETTINGS_FILE")
else
    echo "WARNUNG: jq nicht gefunden. Lese Arrays unsicher aus (kann bei Leerzeichen scheitern)."
    SYMBOLS=$(get_setting "['optimization_settings', 'symbols_to_optimize']" | tr -d "[]',\"")
    TIMEFRAMES=$(get_setting "['optimization_settings', 'timeframes_to_optimize']" | tr -d "[]',\"")
fi
SYMBOLS=${SYMBOLS:-"BTC ETH"} # Fallback, falls leer
TIMEFRAMES=${TIMEFRAMES:-"1h 4h"} # Fallback, falls leer

LOOKBACK_DAYS=$(get_setting "['optimization_settings', 'lookback_days']")
LOOKBACK_DAYS=${LOOKBACK_DAYS:-$DEFAULT_LOOKBACK}
START_CAPITAL=$(get_setting "['optimization_settings', 'start_capital']")
START_CAPITAL=${START_CAPITAL:-$DEFAULT_START_CAPITAL}
N_CORES=$(get_setting "['optimization_settings', 'cpu_cores']")
N_CORES=${N_CORES:-$DEFAULT_CORES}
N_TRIALS=$(get_setting "['optimization_settings', 'num_trials']")
N_TRIALS=${N_TRIALS:-$DEFAULT_TRIALS}

MAX_DD=$(get_setting "['optimization_settings', 'constraints', 'max_drawdown_pct']")
MAX_DD=${MAX_DD:-$DEFAULT_MAX_DD}
MIN_WR=$(get_setting "['optimization_settings', 'constraints', 'min_win_rate_pct']")
MIN_WR=${MIN_WR:-$DEFAULT_MIN_WR}
MIN_PNL=$(get_setting "['optimization_settings', 'constraints', 'min_pnl_pct']")
MIN_PNL=${MIN_PNL:-$DEFAULT_MIN_PNL}

START_DATE=$(date -d "$LOOKBACK_DAYS days ago" +%F)
END_DATE=$(date +%F)
OPTIM_MODE_ARG=${OPTIM_MODE_ARG:-$DEFAULT_OPTIM_MODE} # Standard ist strict

# --- Pipeline starten ---
echo "Optimierung ist aktiviert. Starte Prozesse..."
echo "Verwende Daten der letzten $LOOKBACK_DAYS Tage ($START_DATE bis $END_DATE)."
echo "Symbole: $SYMBOLS | Zeitfenster: $TIMEFRAMES"
echo "Trials: $N_TRIALS | Kerne: $N_CORES | Startkapital: $START_CAPITAL"

# *** Trainer-Aufruf entfernt ***

echo ">>> Starte Handelsparameter-Optimierung (SMC)..."
python3 "$OPTIMIZER" \
    --symbols "$SYMBOLS" \
    --timeframes "$TIMEFRAMES" \
    --start_date "$START_DATE" \
    --end_date "$END_DATE" \
    --jobs "$N_CORES" \
    --max_drawdown "$MAX_DD" \
    --start_capital "$START_CAPITAL" \
    --min_win_rate "$MIN_WR" \
    --trials "$N_TRIALS" \
    --min_pnl "$MIN_PNL" \
    --mode "$OPTIM_MODE_ARG"

if [ $? -ne 0 ]; then
    echo "Fehler im Optimierer-Skript. Pipeline wird abgebrochen."
    deactivate
    exit 1
fi

deactivate
echo "--- Automatischer Pipeline-Lauf abgeschlossen ---"
